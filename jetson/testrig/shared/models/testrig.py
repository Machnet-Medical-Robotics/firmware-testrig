"""
shared/models/testrig.py
Core domain models for the TestRig system.

Hierarchy:
  TestRun
    └── TestSetRef (references TestSet by ID + assigns BoardConfigId)
          └── TestSet
                └── TestCaseRef (references TestCase by ID)
                      └── TestCase
                            └── Step (DSL)

Each layer is owned by a different component:
  TestRun     → Manager   (scheduling, firmware, policy)
  TestSet     → Controller (grouping, board binding)
  TestCase    → Worker    (DSL execution)
  Step        → Worker/Daemon (hardware interaction)
"""

from __future__ import annotations
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, Field
from shared.enums import (
    BoardPairState, FailureType,
    StepStatus, TestCaseStatus, TestSetStatus, TestRunStatus
)
from shared.models.steps import StepUnion


# ---------------------------------------------------------------------------
# Board Config
# Defines the ESP32 DIP switch configuration for a board pair.
#
# Extension guide — adding config parameters:
#   Add fields here. The Controller reads this to call ESP32 config API.
#   The Worker and Daemon never see BoardConfig directly.
# ---------------------------------------------------------------------------

class BoardConfig(BaseModel):
    """
    Represents an ESP32 DIP switch configuration.

    board_config_id   — unique identifier, referenced by TestSetRef.
                        e.g. "SH1", "core2-default".
                        CSV uses this ID directly.

    dip_switch_byte   — single byte (0x00–0xFF) representing all 8 DIP
                        switches as a bitmask. Bit 0 = SW1, Bit 7 = SW8.
                        e.g. 0xA3 = 0b10100011 → SW1, SW2, SW6, SW8 ON.
                        Store as int in JSON (163 for 0xA3), display as hex
                        in logs. The ESP32 receives this byte over UART.

    expected_board_identity — string the Robot PCB announces over UART after
                        reboot to confirm the DIP config loaded correctly.
                        e.g. "shuttle", "core2", "manifold".
                        If the announced identity does not match this value,
                        the Controller raises HARDWARE_FAIL and skips all
                        TestSets bound to this config.
                        Empty string = skip identity check (not recommended).

    esp32_uart_port   — placeholder for when ESP32 UART comms is implemented.
                        e.g. "/dev/ttyUSB1". Not used by mock daemon.

    description       — human-readable label for reports and logs.
    """
    board_config_id:          str
    dip_switch_byte:          int            = 0x00   # stored as int, e.g. 163 for 0xA3
    expected_board_identity:  str            = ""
    esp32_uart_port:          Optional[str]  = None   # e.g. "/dev/ttyUSB1", future use
    description:              Optional[str]  = None

    def dip_switch_hex(self) -> str:
        """Returns the byte as a formatted hex string for logging, e.g. '0xA3'."""
        return f"0x{self.dip_switch_byte:02X}"

    def dip_switch_bits(self) -> dict[str, bool]:
        """
        Returns a human-readable switch map for logging/reports.
        e.g. {"SW1": True, "SW2": True, "SW3": False, ...}
        """
        return {f"SW{i+1}": bool(self.dip_switch_byte & (1 << i)) for i in range(8)}


# ---------------------------------------------------------------------------
# Board Pair
# Runtime state of a physical board pair (Robot PCB + Monitoring PCB).
# ---------------------------------------------------------------------------

class BoardPair(BaseModel):
    board_pair_id:    str
    robot_board_id:   str
    monitor_board_id: str
    current_config:   Optional[str]  = None   # board_config_id currently applied
    state:            BoardPairState = BoardPairState.IDLE


# ---------------------------------------------------------------------------
# Step Result — outcome of a single executed step
# ---------------------------------------------------------------------------

class StepResult(BaseModel):
    step_id:      int
    status:       StepStatus
    failure_type: Optional[FailureType] = None
    actual_value: Optional[str]         = None   # What the device actually returned
    expected:     Optional[str]         = None   # What we expected
    error_detail: Optional[str]         = None   # INFRA/SYSTEM error message
    duration_ms:  Optional[int]         = None


# ---------------------------------------------------------------------------
# TestCase
# Owned by Worker. Contains the full DSL step list.
#
# Assembly extension guide:
#   A new assembly (e.g. front clamp, PC rollers) is just a new set of
#   TestCase JSON files in definitions/testcases/<assembly_name>/.
#   Each TestCase references step commands and channel names registered
#   by that assembly's firmware. No Python code changes needed.
# ---------------------------------------------------------------------------

class TestCaseConfig(BaseModel):
    echo_console: bool = True   # Log MESSAGE steps to output


class OnFailCommand(BaseModel):
    """
    Command to execute on the device if the TestCase fails.
    Typically used to return the board to a safe state.
    e.g. {"command": "com_change_mode", "command_param": "2"}
    """
    command:       str
    command_param: Optional[str] = None


class TestCase(BaseModel):
    schema_version: str          = "1.0"
    test_case_id:   str
    metadata: Optional[dict]     = None
    config:   TestCaseConfig     = Field(default_factory=TestCaseConfig)
    steps:    List[StepUnion]    = Field(default_factory=list)
    on_fail:  List[OnFailCommand] = Field(default_factory=list)


class TestCaseRef(BaseModel):
    """Reference from TestSet → TestCase. Order determines execution sequence."""
    test_case_id: str
    order:        int


# ---------------------------------------------------------------------------
# TestCase Result — aggregated from StepResults
# ---------------------------------------------------------------------------

class TestCaseResult(BaseModel):
    test_case_id: str
    status:       TestCaseStatus
    failure_type: Optional[FailureType] = None
    step_results: List[StepResult]      = Field(default_factory=list)
    duration_ms:  Optional[int]         = None
    error_detail: Optional[str]         = None



# ---------------------------------------------------------------------------
# Board Identity + Discovery
#
# After the Controller applies a BoardConfig and reboots the Robot PCB,
# the board announces its identity over UART. This is cross-checked against
# BoardConfig.expected_board_identity before the Worker is spawned.
#
# After the Worker starts, it runs GScope channel/command discovery once.
# The daemon sends a discovery request over UART; the board responds with
# the list of available channels and commands for this firmware build.
# The Worker caches this and validates TestCase steps against it.
#
# NOTE: The actual UART discovery handshake protocol is NOT implemented yet
# (Monitoring PCB firmware not built). The mock daemon returns a hardcoded
# DiscoveryResult. These models define the interface contract.
# ---------------------------------------------------------------------------

class DiscoveredChannel(BaseModel):
    """
    A single GScope channel discovered from the board.

    name         — channel name as registered in firmware, e.g. "stepper1_controller".
    num_fields   — number of float values in each sample (offset count).
                   e.g. stepper1_controller has 6 fields (pos, vel, accel, ...).
    description  — optional human-readable label from firmware registration.
    """
    name:        str
    num_fields:  int
    description: Optional[str] = None


class DiscoveredCommand(BaseModel):
    """
    A single GScope command discovered from the board.

    name        — command name, e.g. "com_leadscrew_go", "on_demand_leadscrew".
    has_params  — True if the command accepts parameter string.
    description — optional label.
    """
    name:        str
    has_params:  bool          = False
    description: Optional[str] = None


class DiscoveryResult(BaseModel):
    """
    Result of the GScope channel/command discovery handshake.
    Populated by the Hardware Daemon after querying the board over UART.
    Cached by the Worker for the duration of the config group execution.

    board_identity   — the identity string the board announced after reboot
                       e.g. "shuttle". Cross-checked against
                       BoardConfig.expected_board_identity.
    channels         — list of available GScope channels.
    commands         — list of available GScope commands.
    raw_response     — raw UART response string, preserved for debugging.

    Mock behaviour (Phase 3):
      The mock daemon returns a hardcoded DiscoveryResult matching the
      shuttle assembly channels and commands. When real firmware is ready,
      the daemon populates this from the actual UART handshake.
    """
    board_identity: str                      = ""
    channels:       List[DiscoveredChannel]  = Field(default_factory=list)
    commands:       List[DiscoveredCommand]  = Field(default_factory=list)
    raw_response:   Optional[str]            = None

    def has_channel(self, name: str) -> bool:
        return any(c.name == name for c in self.channels)

    def has_command(self, name: str) -> bool:
        return any(c.name == name for c in self.commands)

    def channel_num_fields(self, name: str) -> Optional[int]:
        for c in self.channels:
            if c.name == name:
                return c.num_fields
        return None


# ---------------------------------------------------------------------------
# TestSet
# Owned by Controller. Binds TestCases to a board type and execution policy.
#
# Assembly extension guide:
#   Each assembly gets its own TestSet JSON files in
#   definitions/testsets/<assembly_name>/.
#   Multiple assemblies can be tested in one TestRun by referencing
#   multiple TestSetRefs in the TestRun, each with their own BoardConfigId.
# ---------------------------------------------------------------------------

class BoardBinding(BaseModel):
    """
    Describes which board type this TestSet targets.
    requires_monitor: if True, the Monitoring PCB must be present and ready.
    """
    board_type:       str  = "robot"
    requires_monitor: bool = True


class ExecutionConstraints(BaseModel):
    requires_exclusive_access: bool = True
    stop_on_failure:           bool = False   # Stop remaining TestCases on first failure


class TestSet(BaseModel):
    schema_version:       str                  = "1.0"
    test_set_id:          str
    metadata:             Optional[dict]        = None
    board_binding:        BoardBinding          = Field(default_factory=BoardBinding)
    execution_constraints: ExecutionConstraints = Field(default_factory=ExecutionConstraints)
    test_cases:           List[TestCaseRef]     = Field(default_factory=list)


# ---------------------------------------------------------------------------
# TestSet Result — aggregated from TestCaseResults
# ---------------------------------------------------------------------------

class TestSetResult(BaseModel):
    test_set_id:       str
    status:            TestSetStatus
    failure_type:      Optional[FailureType]   = None
    test_case_results: List[TestCaseResult]    = Field(default_factory=list)
    duration_ms:       Optional[int]           = None
    error_detail:      Optional[str]           = None


# ---------------------------------------------------------------------------
# TestRun
# Owned by Manager. Top-level scheduling and firmware selection.
# ---------------------------------------------------------------------------

class FirmwareRef(BaseModel):
    repository:  str
    commit_hash: str
    branch:      Optional[str] = None


class ExecutionPolicy(BaseModel):
    max_parallel_board_pairs:         int  = 1
    retry_on_infra_failure:           int  = 1     # Worker retry count
    abort_on_critical_infra_failure:  bool = True
    stop_on_channel_validation_error: bool = False
    # False (default): TestCases with missing channels are marked INFRA_FAIL
    # with a clear message, but other TestCases in the group still run.
    # True: any channel validation failure aborts the entire config group.


class TestSetRef(BaseModel):
    """
    Links a TestSet to a specific board config within a TestRun.
    The Controller uses board_config_id to group TestSets for batching.
    Priority determines execution order within the same config group.
    """
    test_set_id:     str
    board_config_id: str
    priority:        int = 1


class TestRunMetadata(BaseModel):
    requested_by: str
    labels:       List[str]       = Field(default_factory=list)
    created_at:   Optional[datetime] = None


class TestRun(BaseModel):
    schema_version:   str              = "1.0"
    test_run_id:      str
    metadata:         TestRunMetadata
    firmware:         FirmwareRef
    execution_policy: ExecutionPolicy  = Field(default_factory=ExecutionPolicy)
    test_set_refs:    List[TestSetRef] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# TestRun Result — top-level report output
# ---------------------------------------------------------------------------

class ConfigGroupResult(BaseModel):
    """
    Results for all TestSets that shared the same (board_pair_id, board_config_id).
    One Worker execution per ConfigGroup.
    """
    board_pair_id:    str
    board_config_id:  str
    test_set_results: List[TestSetResult] = Field(default_factory=list)
    worker_retries:   int                 = 0
    failure_type:     Optional[FailureType] = None
    error_detail:     Optional[str]         = None


class TestRunResult(BaseModel):
    test_run_id:          str
    status:               TestRunStatus
    firmware_commit_hash: str
    started_at:           Optional[datetime]     = None
    completed_at:         Optional[datetime]      = None
    config_group_results: List[ConfigGroupResult] = Field(default_factory=list)
    system_fault_detail:  Optional[str]           = None
