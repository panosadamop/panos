"""Domain-level exceptions."""


class DomainError(Exception):
    """Base class for domain rule violations."""


class EntityNotFoundError(DomainError):
    def __init__(self, kind: str, entity_id: str) -> None:
        super().__init__(f"{kind} {entity_id!r} not found")
        self.kind = kind
        self.entity_id = entity_id
