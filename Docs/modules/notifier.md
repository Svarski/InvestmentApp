# Notifier

## Role

Handles delivery of alerts.

## Channels

* email
* telegram

## Flow

AlertEngine → MultiNotifier → channels

## Rules

* must respect ALERT_CHANNEL
* must support retry
* must not bypass MultiNotifier

## Do NOT

* send alerts directly
* bypass config
