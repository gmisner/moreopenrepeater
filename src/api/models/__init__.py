"""Pydantic request/response models for the repeater API.

Split by domain; this package re-exports everything so
``from api.models import X`` keeps working.
"""
from __future__ import annotations

from .types import (
    NetLinks,
    WeatherSeverity,
    CosSource,
    Polarity,
    PttOutput,
    LinkRadioPtt,
    SerialInputLine,
    SerialOutputLine,
    PiGpioPin,
    SerialDevice,
    Role,
    PublicPageMode,
    MonitorSquelch,
    ListenSource,
    RecordingSource,
    TranscriptionEngine,
    GpioPinNumber,
    FanOutput,
    NodeNumber,
    hh_mm,
    weekdays,
    MAX_ANNOUNCEMENT_LENGTH,
    UpdateChannel,
    HOMEASSISTANT_TARGET,
    _SOURCE,
    _URL_OR_EMPTY,
    _ONE_LINE,
    _CONF_TEXT,
)

from .config import (
    StatusResponse,
    GpioPinConfig,
    MetarAirport,
    SiteSensor,
    LinkFavorite,
    LinkSchedule,
    NetSchedule,
    GpioSchedule,
    ConfigResponse,
    ConfigUpdateRequest,
)

from .autopatch import (
    AutopatchDialRequest,
    AutopatchCallResponse,
    AutopatchStatusResponse,
    SipTrunkRequest,
    SipTrunkSettings,
    SipTrunkStatusResponse,
)

from .simulate import (
    SimulateCOSRequest,
    SimulateCTCSSRequest,
    SimulateDTMFRequest,
    SimulateRemoteKeyedRequest,
)

from .programming import (
    MacroResponse,
    MacroCreateRequest,
    AnnouncementFields,
    AnnouncementModel,
    AnnouncementResponse,
    SnapshotModel,
    SnapshotImportRequest,
)

from .admin import (
    SavedBackupResponse,
    BackupFolderResponse,
    BackupRestoreResponse,
    CommitSummary,
    UpdateRunStatus,
    AutoUpdateSettings,
    AutoUpdateStatus,
    UpdatesResponse,
    UpdateCheckResponse,
    UpdateRequest,
    TailscaleRunStatus,
    TailscaleResponse,
    TailscaleUpRequest,
    TailscaleServeRequest,
    AlertSettingsResponse,
    AlertSettingsRequest,
    SentAlert,
    AlertsResponse,
    AlertTestResponse,
    LogLineResponse,
    LogsSinceResponse,
    LogShippingSettingsResponse,
    LogShippingSettingsRequest,
    LogShippingResponse,
    LogShippingTestResponse,
    RestartResponse,
)

from .weather import (
    WeatherAlertResponse,
    WeatherStatusResponse,
)

from .activity import (
    ActivityDay,
    RecordingResponse,
    ActivitySummaryResponse,
    TransmissionResponse,
)

from .audio import (
    AudioPreviewRequest,
    TTSInfoResponse,
    AudioDeviceResponse,
    SerialPortResponse,
    BoardResponse,
    BoardApplyRequest,
    MixerResultResponse,
    BoardApplyResponse,
    SoundLevelResponse,
    SoundLevelsResponse,
    SoundLevelUpdateRequest,
    LinkAudioResponse,
    ReceiverOpeningResponse,
    AudioGlitchResponse,
    AudioEngineResponse,
)

from .linking import (
    AllStarNodeInfo,
    AllStarStatusResponse,
    GpioPinStatus,
    GpioScheduleStatus,
    FanStatus,
    SensorHistoryPoint,
    SensorStatus,
    SensorsStatusResponse,
    GpioStatusResponse,
    GpioOutputRequest,
    LinkInfo,
    LinkScheduleStatus,
    LinksResponse,
    LinkConnectRequest,
    AllStarNodeRequest,
    EchoLinkRequest,
    EchoLinkStationInfo,
    EchoLinkStatusResponse,
)

from .auth import (
    LoginRequest,
    SessionResponse,
    UserResponse,
    TokenResponse,
    AgentAccessResponse,
    AgentAccessRequest,
    TokenCreateRequest,
    TokenCreatedResponse,
    UserCreateRequest,
    UserUpdateRequest,
)

from .aprs import (
    AprsStationResponse,
    AprsMapResponse,
)

from .site import (
    AuditEntryResponse,
    AssetResponse,
    NetCheckIn,
    NetSummary,
    RunningNet,
    PastNet,
    NetStatusResponse,
    NetStartRequest,
    NetCheckInRequest,
    ControlCodeStatus,
    ControlCodeSetup,
    ControlCodeConfirmRequest,
    ControlCodeUser,
    HomeAssistantStatus,
    HomeAssistantTestRequest,
    PublicStatus,
    StreamSettingsRequest,
    StreamStatus,
    StreamResponse,
    MailboxBoxRequest,
    MailboxBoxResponse,
    MailboxMessageResponse,
    TranscriptionStatus,
    MailboxResponse,
    LinkRadioStatus,
    MonitorReceiverStatus,
    HubSiteResponse,
    HubSiteCreateRequest,
    HubSiteUpdateRequest,
    HubSiteStatusResponse,
)

__all__ = ['NetLinks', 'WeatherSeverity', 'CosSource', 'Polarity', 'PttOutput', 'LinkRadioPtt', 'SerialInputLine', 'SerialOutputLine', 'PiGpioPin', 'SerialDevice', 'Role', 'PublicPageMode', 'MonitorSquelch', 'ListenSource', 'RecordingSource', 'TranscriptionEngine', 'GpioPinNumber', 'FanOutput', 'NodeNumber', 'hh_mm', 'weekdays', 'MAX_ANNOUNCEMENT_LENGTH', 'UpdateChannel', 'HOMEASSISTANT_TARGET', '_SOURCE', '_URL_OR_EMPTY', '_ONE_LINE', '_CONF_TEXT', 'StatusResponse', 'GpioPinConfig', 'MetarAirport', 'SiteSensor', 'LinkFavorite', 'LinkSchedule', 'NetSchedule', 'GpioSchedule', 'ConfigResponse', 'ConfigUpdateRequest', 'AutopatchDialRequest', 'AutopatchCallResponse', 'AutopatchStatusResponse', 'SipTrunkRequest', 'SipTrunkSettings', 'SipTrunkStatusResponse', 'SimulateCOSRequest', 'SimulateCTCSSRequest', 'SimulateDTMFRequest', 'SimulateRemoteKeyedRequest', 'MacroResponse', 'MacroCreateRequest', 'AnnouncementFields', 'AnnouncementModel', 'AnnouncementResponse', 'SnapshotModel', 'SnapshotImportRequest', 'SavedBackupResponse', 'BackupFolderResponse', 'BackupRestoreResponse', 'CommitSummary', 'UpdateRunStatus', 'AutoUpdateSettings', 'AutoUpdateStatus', 'UpdatesResponse', 'UpdateCheckResponse', 'UpdateRequest', 'TailscaleRunStatus', 'TailscaleResponse', 'TailscaleUpRequest', 'TailscaleServeRequest', 'AlertSettingsResponse', 'AlertSettingsRequest', 'SentAlert', 'AlertsResponse', 'AlertTestResponse', 'LogLineResponse', 'LogsSinceResponse', 'LogShippingSettingsResponse', 'LogShippingSettingsRequest', 'LogShippingResponse', 'LogShippingTestResponse', 'RestartResponse', 'WeatherAlertResponse', 'WeatherStatusResponse', 'ActivityDay', 'RecordingResponse', 'ActivitySummaryResponse', 'TransmissionResponse', 'AudioPreviewRequest', 'TTSInfoResponse', 'AudioDeviceResponse', 'SerialPortResponse', 'BoardResponse', 'BoardApplyRequest', 'MixerResultResponse', 'BoardApplyResponse', 'SoundLevelResponse', 'SoundLevelsResponse', 'SoundLevelUpdateRequest', 'LinkAudioResponse', 'ReceiverOpeningResponse', 'AudioGlitchResponse', 'AudioEngineResponse', 'AllStarNodeInfo', 'AllStarStatusResponse', 'GpioPinStatus', 'GpioScheduleStatus', 'FanStatus', 'SensorHistoryPoint', 'SensorStatus', 'SensorsStatusResponse', 'GpioStatusResponse', 'GpioOutputRequest', 'LinkInfo', 'LinkScheduleStatus', 'LinksResponse', 'LinkConnectRequest', 'AllStarNodeRequest', 'EchoLinkRequest', 'EchoLinkStationInfo', 'EchoLinkStatusResponse', 'LoginRequest', 'SessionResponse', 'UserResponse', 'TokenResponse', 'AgentAccessResponse', 'AgentAccessRequest', 'TokenCreateRequest', 'TokenCreatedResponse', 'UserCreateRequest', 'UserUpdateRequest', 'AprsStationResponse', 'AprsMapResponse', 'AuditEntryResponse', 'AssetResponse', 'NetCheckIn', 'NetSummary', 'RunningNet', 'PastNet', 'NetStatusResponse', 'NetStartRequest', 'NetCheckInRequest', 'ControlCodeStatus', 'ControlCodeSetup', 'ControlCodeConfirmRequest', 'ControlCodeUser', 'HomeAssistantStatus', 'HomeAssistantTestRequest', 'PublicStatus', 'StreamSettingsRequest', 'StreamStatus', 'StreamResponse', 'MailboxBoxRequest', 'MailboxBoxResponse', 'MailboxMessageResponse', 'TranscriptionStatus', 'MailboxResponse', 'LinkRadioStatus', 'MonitorReceiverStatus', 'HubSiteResponse', 'HubSiteCreateRequest', 'HubSiteUpdateRequest', 'HubSiteStatusResponse']
