# Testfiles Overview
Sequence
```
input (TestRun CSV/call predefined TestRun)
```
```text
Schema supports:
- scheduling → TestRun
- execution orchestration → TestSet
- execution logic → TestCase
- hardware abstraction → NOT in schema (lives in daemon + step mapping)
```
- [TestRun](#1-TestRun-Orchestration-Layer)
- [TestSet](#2-TestSet-Execution-Groupin-Layer)
- [TestCase](#3-TestCase-Execution-Logic-Layer)
- [TestSteps](#4-TestStep-DSL-Layer–MOST-IMPORTANT)
- [Format Summary](#5-Format-Summary)
- [Directory Format](#6-Folder-Structure)

## 1. TestRun (Orchestration Layer)
There will be 2 types of testrun
predefined: 
adhoc: CSV

### Ownership

TestRun Manager

---

### Purpose

* USer Input
* Scheduling
* Firmware selection
* High-level grouping

### File naming

```
Description + Date + RequestorID
regression_260426_FW
```

### SCHEMA

```json
{
  "SchemaVersion": "1.0",

  "TestRunId": "TR-2026-04-00123",

  "Metadata": {
    "RequestedBy": "team-alpha",
    "Labels": ["regression", "long-running"],
    "CreatedAt": "2026-04-30T10:00:00Z"
  },

  "Firmware": {
    "Repository": "robot-firmware",
    "CommitHash": "a1b2c3d4",
    "Branch": "main"
  },

  "ExecutionPolicy": {
    "MaxParallelBoardPairs": 3,
    "RetryOnInfraFailure": 1,
    "AbortOnCriticalInfraFailure": true
  },

  "TestSetRefs": [
    {
      "TestSetId": "TS-ENCODER-CORE2",
      "ConfigId": "core2-default",
      "Priority": 1
    }
  ]
}
```

or CSV
```
FirmwareHash,TestSets,BoardConfigId
a1b2c3d4,TS-ENCODER,SH1
a1b2c3d4,TS-Motor,SH1
```

Request predefined testrun
```
TemplateId,FirmwareOverrideHash,Priority
REGRESSION_SUITE,a1b2c3d4,HIGH
```

Template? 
```
{
  "TemplateId": "REGRESSION_SUITE",
  "TestSetRefs": [
    { "TestSetId": "TS-ENCODER", "ConfigId": "core2-default" },
    { "TestSetId": "TS-MOTOR", "ConfigId": "core2-default" }
  ]
}
```

## 2. TestSet (Execution Grouping Layer)

### Ownership

Controller

### Purpose

* Bind to **BoardPair + Config**
* Define execution grouping

### SCHEMA

```json
{
  "SchemaVersion": "1.0",

  "TestSetId": "TS-ENCODER-CORE2",

  "Metadata": {
    "Description": "Core2 encoder validation tests"
  },

  "BoardBinding": {
    "BoardType": "robot",
    "RequiresMonitor": true
  },

  "ExecutionConstraints": {
    "RequiresExclusiveAccess": true,
    "StopOnFailure": false
  },

  "TestCases": [
    {
      "TestCaseRef": "TC-CORE2-BRUSHED1-ENCODER",
      "Order": 1
    }
  ]
}
```

## 3. TestCase (Execution Logic Layer)
**Currently full DSL in TestCase**
### Ownership

Worker

### Purpose

* Define **test logic**
* Interpreted at runtime

### SCHEMA

```json
{
  "SchemaVersion": "1.0",

  "TestCaseId": "TC-CORE2-BRUSHED1-ENCODER",

  "Metadata": {
    "TestName": "Core2 brushed1 motor encoder Test",
    "Description": "Verify encoder responds to rotation",
    "Tags": ["encoder", "motor"],
    "EstimatedDurationMs": 180000
  },

  "Config": {
    "EchoConsole": true
  },

  "Steps": [
    {
      "StepId": 1,
      "Type": "REF",
      "Ref": "common.enter_restricted_mode"
    },
    {
      "StepId": 2,
      "Type": "MESSAGE",
      "Message": "Rotate motor CLOCKWISE",
      "TimeoutMs": 30000,
      "StopIfFail": true
    },
    {
      "StepId": 3,
      "Type": "CHANNEL_WAIT",
      "Channel": "brushed1_encoder",
      "Expected": {
        "Min": 25,
        "Max": 250
      },
      "TimeoutMs": 60000,
      "StopIfFail": true
    },
    {
      "StepId": 4,
      "Type": "WAIT",
      "TimeoutMs": 500
    }
  ],

  "OnFail": [
    {
      "Type": "COMMAND",
      "Command": "com_change_mode 2"
    }
  ]
}
```


## 4. TestStep (DSL Layer)
**Optional for Now**

### Standard Step Types

| Type           | Meaning                 |
| -------------- | ----------------------- |
| `MESSAGE`      | human instruction       |
| `WAIT`         | delay                   |
| `COMMAND`      | send to device          |
| `CHANNEL_WAIT` | condition evaluation    |
| `REF`          | include reusable script |

**TestSet and accompanying TestCase**
``` JSON
{
  "StepId": "STEP-VERIFY-MOTOR-CW",

  "Description": "Rotate motor and verify encoder increases",

  "Steps": [
    {
      "Type": "COMMAND",
      "Command": "motor_rotate",
      "Params": {
        "speed": "$speed"
      }
    },
    {
      "Type": "WAIT",
      "TimeoutMs": 500
    },
    {
      "Type": "CHANNEL_WAIT",
      "Channel": "encoder",
      "Expected": {
        "Min": "$min",
        "Max": "$max"
      },
      "TimeoutMs": 5000
    }
  ]
}
```
and TestCase:
``` JSON
{
  "Steps": [
    {
      "Ref": "STEP-VERIFY-MOTOR-CW",
      "Args": {
        "speed": 300,
        "min": 25,
        "max": 250
      }
    }
  ]
}
```

### Retry Policy

```json
"Retry": {
  "MaxAttempts": 2,
  "DelayMs": 1000
}
```

### Failure Classification

```json
"FailureType": "TEST" 
```

or

```json
"FailureType": "INFRA"
```

Must distinguish between:

| Type     | Meaning        |
| -------- | -------------- |
| TEST     | firmware issue |
| INFRA    | system issue   |
| HARDWARE | board issue    |


## 5. Format Summary

| Entity           | Format | Note                     |
| ---------------- | ------ | ------------------------ |
| TestRun          | JSON   | structured orchestration |
| TestRun (Input)  | CSV    | structured orchestration |
| TestCase         | JSON   | DSL execution            |

Future:
| Entity           | Format | Note                     |
| ---------------- | ------ | ------------------------ |
| TestRun          | JSON   | structured orchestration |
| TestRun (Input)  | CSV    | structured orchestration |
| TestSet          | JSON   | reusable grouping        |
| TestCase         | JSON   | DSL execution            |

```text
CSV → ingestion layer → convert → JSON TestRun
```

## 6. Folder Structure

```text id="repo_layout"
testrig/
  definitions/
    testruns/
      TR-2026-04-00123.json

    testsets/
      core2/
        encoder.json

    testcases/
      core2/
        brushed1_encoder.json

    steps/
      common/
        enter_restricted_mode.json

    configs/
      core2-default.json
```

---


> Use JSON for all core schemas, treat TestCase as a DSL, keep hardware out of the schema, and let TestRun/TestSet define scheduling while Worker interprets execution.



Whats Needed
**full JSON schema (with validation rules)**
**TestCase DSL**
**Python parser/execution engine for TestCase DSL**
**test report schema**
