# Worker

## Role

Background process that runs continuously and drives the system.

## Responsibilities

* fetch market data
* run alert engine
* send notifications
* store data to DB

## Behavior

* infinite loop
* sleep between cycles
* must never crash

## Important Rules

* independent from UI
* safe execution (try/except)
* no heavy computation

## Do NOT

* move logic into UI
* block execution
* introduce instability
