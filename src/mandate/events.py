from enum import StrEnum


class EventType(StrEnum):
    MANDATE_ISSUED = "mandate_issued"
    MANDATE_REVOKED = "mandate_revoked"
    PAYMENT_REQUESTED = "payment_requested"
    DECISION_MADE = "decision_made"
    PAYMENT_APPROVED = "payment_approved"
    PAYMENT_REJECTED = "payment_rejected"
    ORDER_CREATED = "order_created"
    PAYMENT_EXECUTED = "payment_executed"
    PAYMENT_FAILED = "payment_failed"
    WEBHOOK_RECEIVED = "webhook_received"
    KILL_SWITCH = "kill_switch"
