from enum import StrEnum


class TransactionType(StrEnum):
    CREDIT = "CREDIT"
    DEBIT = "DEBIT"
    TRANSFER_OUT = "TRANSFER_OUT"
    TRANSFER_IN = "TRANSFER_IN"
    CONVERSION_OUT = "CONVERSION_OUT"
    CONVERSION_IN = "CONVERSION_IN"

    @property
    def is_outflow(self) -> bool:
        return self in {self.DEBIT, self.TRANSFER_OUT, self.CONVERSION_OUT}


class TransferStatus(StrEnum):
    COMPLETED = "COMPLETED"
