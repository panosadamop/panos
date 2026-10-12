"""Connector accounts and knowledge collections (M8, ADR-026).

A connector account is a link to an external service (Google, Microsoft, Jira, Azure
DevOps). Only non-secret facts live in the database; tokens and API keys are in the OS
keyring under ``connector:<id>``. Content read through an account is untrusted and carries
the account's privacy label (LOCAL_ONLY unless the owner relaxes it).

A knowledge collection is an indexed source outside any project: a company folder, a list
of websites, or a Google Drive / OneDrive / SharePoint folder (FR-019).
"""

from enum import StrEnum
from typing import Annotated, Any

from pydantic import Field

from panos_core.domain.clock import utc_now
from panos_core.domain.enums import PrivacyMode
from panos_core.domain.ids import new_id
from panos_core.domain.models import AwareDatetime, Entity, NonEmptyStr, _id_of

ConnectorId = Annotated[str, _id_of("connector")]
CollectionId = Annotated[str, _id_of("collection")]


class ConnectorKind(StrEnum):
    GOOGLE = "google"  # Gmail, Google Calendar, Google Drive
    MICROSOFT = "microsoft"  # OneDrive / SharePoint (Graph)
    JIRA = "jira"  # Jira Cloud
    AZURE_DEVOPS = "azure_devops"


class ConnectorStatus(StrEnum):
    CONNECTED = "CONNECTED"
    NEEDS_REAUTH = "NEEDS_REAUTH"  # refresh failed or token revoked
    DISCONNECTED = "DISCONNECTED"


class ConnectorAccount(Entity):
    id: ConnectorId = Field(default_factory=lambda: new_id("connector"))
    kind: ConnectorKind
    label: NonEmptyStr  # e.g. "me@gmail.com", "acme.atlassian.net"
    base_url: str | None = None  # Jira site / Azure DevOps organization URL
    scopes: list[str] = Field(default_factory=list)  # granted OAuth scopes or features
    privacy_mode: PrivacyMode = PrivacyMode.LOCAL_ONLY
    status: ConnectorStatus = ConnectorStatus.CONNECTED
    created_at: AwareDatetime = Field(default_factory=utc_now)
    last_used_at: AwareDatetime | None = None

    @property
    def secret_name(self) -> str:
        return f"connector:{self.id}"


class CollectionKind(StrEnum):
    FOLDER = "folder"
    WEBSITE = "website"
    GOOGLE_DRIVE = "google_drive"
    ONEDRIVE = "onedrive"  # OneDrive and SharePoint document libraries (Graph drives)


class KnowledgeCollection(Entity):
    id: CollectionId = Field(default_factory=lambda: new_id("collection"))
    name: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,63}$")]
    kind: CollectionKind
    # Folder path, list of URLs, or a remote folder reference ({drive_id, item_id} / folder id).
    location: dict[str, Any] = Field(default_factory=dict)
    connector_id: ConnectorId | None = None
    privacy_mode: PrivacyMode = PrivacyMode.LOCAL_ONLY
    enabled: bool = True
    created_at: AwareDatetime = Field(default_factory=utc_now)
    last_indexed_at: AwareDatetime | None = None
