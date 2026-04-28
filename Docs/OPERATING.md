# System Operation Outline

## System Responsibility

- Scheduling logic
* board assignment
* config matching
* sequential testset execution
- Execution state
* where in the test hierarchy you are
* retry rules
* abort conditions
- Orchestration
* calling Test Service
* sequencing commands
* receiving normalized results
* interpreting responses
- Interface through predefined *CAIRS-embedded-software* UART see gscope-app (e.g framing uart and CAN packet)

## Operation Sequence Diagram

This reflects *runtime flow across Manager → Controller → Service → Hardware → back*.

```mermaid
sequenceDiagram
    autonumber

    participant AM as App Management
    participant M as Test Run Manager
    participant C as Test Run Controller
    participant S as Test Service
    participant P as Plugin Layer
    participant HW as Robot + Monitor Boards

    AM->>M: start/monitor daemon
    AM->>C: start/monitor daemon
    AM->>S: start/monitor daemon

    M->>M: ingest CSV test run
    M->>M: resolve firmware git commit
    M->>C: dispatch TestRun(job)

    C->>C: assign to available BoardPair
    C->>C: sort by board_id + config match

    loop per BoardPair (parallel)
        C->>S: execute Testset(batch, board_config)

        loop per Testset (sequential)
            S->>S: load testcases + scripts

            loop per Testcase
                S->>P: map script → hardware command
                P->>HW: UART command (existing protocol)
                HW-->>P: UART response
                P-->>S: normalized result

                S->>S: attach result to testcase step
            end

            S->>C: return testset result
        end
    end

    C->>C: aggregate results (testset → run)
    C->>M: final report(TestRunResult)

    M->>M: store + export report
    AM->>AM: monitor health + restart if needed
```

---

## State machine for Test Run Controller

---

## Controller-level states

Each **BoardPair has its own state machine instance**.

### Global Controller States

```text
IDLE
RUNNING
PAUSED
ERROR
SHUTTING_DOWN
```

But more importantly:

---

## Per-BoardPair Execution State Machine

```mermaid
stateDiagram-v2
    [*] --> Idle

    Idle --> Assigned: TestRun allocated

    Assigned --> Validating: check config match
    Validating --> Ready

    Ready --> Flashing: firmware commit resolved
    Flashing --> Booting: flash success
    Booting --> InitializingHardware

    InitializingHardware --> RunningTestset

    RunningTestset --> RunningTestcase

    RunningTestcase --> RunningTestStep

    RunningTestStep --> RunningTestcase: step complete

    RunningTestcase --> RunningTestset: testcase complete

    RunningTestset --> RunningTestset: next testset

    RunningTestset --> Completed: all testsets done

    RunningTestset --> Failed: unrecoverable error
    Flashing --> Failed
    Booting --> Failed
    InitializingHardware --> Failed

    Failed --> Reporting
    Completed --> Reporting

    Reporting --> Idle
```

---

## Daemon Running state

| Service             | Model                         |
| ------------------- | ----------------------------- |
| App Management      | ALWAYS RUNNING                |
| Test Run Manager    | ALWAYS RUNNING                |
| Test Run Controller | ALWAYS RUNNING                |
| Test Service        | ALWAYS RUNNING per board-pair |
| Monitoring          | ALWAYS RUNNING                |

---