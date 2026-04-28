# Firmware-testrig README

This repository contains all the applications (daemons) that run on the testrig's linux controller. Repository folder structure following modular monolithic structure.

## Daemon Responsibility Allocation

> Monitor does *oversees and orchastrates*
> Controller decides *what: External flow*
> Manager does *what: internal coordination*
> Service decides *how*
> Hardware does *nothing except execute*

## Development Sequence (To be removed with first release)

*3-dimension approach Verticle, Horizontal, Depth*
Verticle: Monterrey(Robot), Castello(Manifold), Sardis(Console)
Horizontal: various daemons, firmwares
Depth: fidelity of functions, error, logs
---

### Phase 1 — Montery Verticle, full controller Breadth

Ignore parallelism first.

Build:

* 1 Manager (simple queue)
* 1 Controller (single board only)
* 1 Test Service (basic UART wrapper)
* 1 BoardPair abstraction (even if only 1 exists)

#### Goal:

> Run one firmware test from CSV → hardware → report


---

### Phase 2 — Introduce state machine properly

Refactor Controller into:

* BoardPair state machine
* strict transitions
* no ad-hoc function chaining

#### Add:

* retry rules per state
* failure classification (test failure ≠ system failure)

---

### Phase 3 — Test Service hardening

Stabalise:

* plugin architecture
* hardware abstraction layer
* command normalization

#### Goal:

> Simplify testcapability and hardware expansion

---

### Phase 4 — Parallel BoardPairs

Scale to:

* Castello(Manifold), Sardis(Console) verticles
* 3 independent state machines

#### Add:

* resource allocator
* queue → board mapping logic
* config matching layer

---

### Phase 5 — App Management

Add:

* daemon watchdog
* restart logic
* health checks
* stuck execution detection

---

Implement:

* correlation ID per TestRun
* per BoardPair trace logs
* per testcase step logs