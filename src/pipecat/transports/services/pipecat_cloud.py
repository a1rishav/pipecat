#
# Copyright (c) 2024–2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

import logging
from dataclasses import dataclass
from functools import wraps
from typing import Any, Callable, Optional, Union

from fastapi import WebSocket
from pipecatcloud.agent import DailySessionArguments, SessionArguments, WebSocketSessionArguments
from pydantic import BaseModel, ConfigDict

from pipecat.transports.base_transport import BaseTransport, TransportParams
from pipecat.transports.network.fastapi_websocket import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
    FrameSerializer,
)
from pipecat.transports.network.small_webrtc import (
    SmallWebRTCConnection,
    SmallWebRTCTransport,
)
from pipecat.transports.services.daily import (
    DailyDialinSettings,
    DailyParams,
    DailyTranscriptionSettings,
    DailyTransport,
)

logger = logging.getLogger(__name__)


@dataclass
class WebRTCSessionArguments(SessionArguments):
    """WebRTC based agent session arguments. The arguments are received by the
    bot() entry point.
    """

    webrtc_connection: SmallWebRTCConnection


class PipecatCloudParams(TransportParams):
    """Parameters for PipecatCloudTransport.

    This class combines parameters from all transport types:
    - TransportParams (inherited)
    - FastAPIWebsocketParams
    - DailyParams
    """

    # FastAPIWebsocketParams fields
    add_wav_header: bool = False
    serializer: Optional[FrameSerializer] = None
    session_timeout: Optional[int] = None

    # DailyParams fields
    api_url: str = "https://api.daily.co/v1"
    api_key: str = ""
    dialin_settings: Optional[DailyDialinSettings] = None
    transcription_enabled: bool = False
    transcription_settings: DailyTranscriptionSettings = DailyTranscriptionSettings()

    def to_transport_params(self) -> TransportParams:
        """Convert to TransportParams."""
        return self

    def to_websocket_params(self) -> FastAPIWebsocketParams:
        """Convert to FastAPIWebsocketParams."""
        base_params = self.model_dump()
        # Remove WebSocket-specific fields since we'll add them explicitly
        websocket_fields = ["add_wav_header", "serializer", "session_timeout"]
        for field in websocket_fields:
            base_params.pop(field, None)

        return FastAPIWebsocketParams(
            **base_params,
            add_wav_header=self.add_wav_header,
            serializer=self.serializer,
            session_timeout=self.session_timeout,
        )

    def to_daily_params(self) -> DailyParams:
        """Convert to DailyParams."""
        base_params = self.model_dump()
        # Remove Daily-specific fields since we'll add them explicitly
        daily_fields = [
            "api_url",
            "api_key",
            "dialin_settings",
            "transcription_enabled",
            "transcription_settings",
        ]
        for field in daily_fields:
            base_params.pop(field, None)

        return DailyParams(
            **base_params,
            api_url=self.api_url,
            api_key=self.api_key,
            dialin_settings=self.dialin_settings,
            transcription_enabled=self.transcription_enabled,
            transcription_settings=self.transcription_settings,
        )


class PipecatCloudSessionArguments:
    """Arguments for creating a PipecatCloudTransport session.

    This class can be initialized with arguments for any of the supported transport types:
    - WebSocket: Pass websocket=WebSocket
    - Daily: Pass room_url=str, token=str, bot_name=str
    - WebRTC: Pass webrtc_connection=SmallWebRTCConnection
    """

    def __init__(
        self,
        *,
        websocket: Optional[WebSocket] = None,
        room_url: Optional[str] = None,
        token: Optional[str] = None,
        webrtc_connection: Optional[SmallWebRTCConnection] = None,
        session_id: Optional[str] = None,
        body: Optional[dict] = None,
    ):
        """Initialize session arguments for any supported transport type."""
        if websocket is not None:
            self._args = WebSocketSessionArguments(websocket=websocket, session_id=session_id)
        elif room_url is not None:
            self._args = DailySessionArguments(
                # TODO-CB: bot_name is missing from DailySessionArguments?
                room_url=room_url,
                token=token,
                session_id=session_id,
                body=body,
            )
        elif webrtc_connection is not None:
            self._args = WebRTCSessionArguments(
                webrtc_connection=webrtc_connection, session_id=session_id
            )
        else:
            raise ValueError(
                "Must provide either websocket, (room_url, token, bot_name), or webrtc_connection"
            )

    @property
    def args(self):
        """Get the underlying session arguments."""
        return self._args


class PipecatCloudTransport(BaseTransport):
    """A transport that wraps FastAPIWebsocketTransport, SmallWebRTCTransport, and DailyTransport.

    This transport will instantiate one of the three underlying transports based on the
    session arguments provided to the constructor.

    Event handlers:
        @event_handler("on_client_connected"): Called when a client connects. Maps to:
            - FastAPIWebsocketTransport.event_handler("on_client_connected")
            - SmallWebRTCTransport.event_handler("on_client_connected")
            - DailyTransport.event_handler("on_first_participant_joined")

        @event_handler("on_client_disconnected"): Called when a client disconnects. Maps to:
            - FastAPIWebsocketTransport.event_handler("on_client_disconnected")
            - SmallWebRTCTransport.event_handler("on_client_disconnected")
            - DailyTransport.event_handler("on_participant_left")

        Other event handlers are passed through directly to the underlying transport.

    Args:
        session_args: Arguments for creating the session. The type of arguments determines
            which transport will be used.
        params: Configuration parameters for the transport. Parameters will be extracted
            based on the session arguments type.
        input_name: Optional name for the input transport.
        output_name: Optional name for the output transport.
    """

    # Event name mappings for each transport type
    # Only include events that need to be mapped differently
    _EVENT_MAPPINGS = {
        DailyTransport: {
            "on_client_connected": "on_first_participant_joined",
            "on_client_disconnected": "on_participant_left",
        },
    }

    def __init__(
        self,
        session_args: PipecatCloudSessionArguments,
        params: Optional[Union[PipecatCloudParams, TransportParams]] = None,
        *,
        input_name: Optional[str] = None,
        output_name: Optional[str] = None,
    ):
        super().__init__(input_name=input_name, output_name=output_name)

        # Convert TransportParams to PipecatCloudParams if needed
        if isinstance(params, TransportParams):
            cloud_params = PipecatCloudParams()
            for field_name, field_value in params.model_dump().items():
                setattr(cloud_params, field_name, field_value)
            params = cloud_params
        else:
            params = params or PipecatCloudParams()

        self._pending_handlers = {}

        # Create the appropriate transport based on session arguments type
        args = session_args.args
        if isinstance(args, WebSocketSessionArguments):
            logger.info("Using FastAPIWebsocketTransport")
            websocket_params = params.to_websocket_params()
            self._transport = FastAPIWebsocketTransport(
                args.websocket,
                websocket_params,
                input_name=input_name,
                output_name=output_name,
            )
        elif isinstance(args, DailySessionArguments):
            logger.info("Using DailyTransport")
            daily_params = params.to_daily_params()
            self._transport = DailyTransport(
                args.room_url,
                args.token,
                # TODO-CB: Bot name is missing from DailySessionArguments
                "Bot",
                params=daily_params,
                input_name=input_name,
                output_name=output_name,
            )
        elif isinstance(args, WebRTCSessionArguments):
            logger.info("Using SmallWebRTCTransport")
            transport_params = params.to_transport_params()
            self._transport = SmallWebRTCTransport(
                args.webrtc_connection,
                transport_params,
                input_name=input_name,
                output_name=output_name,
            )
        else:
            raise ValueError(f"Unsupported session arguments type: {type(args)}")

        # Register any handlers that were added before transport creation
        for event_name, handlers in self._pending_handlers.items():
            for handler in handlers:
                self._register_handler(event_name, handler)

    def _register_handler(self, event_name: str, handler: Callable[..., Any]) -> None:
        """Register a handler with the appropriate transport method."""
        transport_type = type(self._transport)

        # If the transport type has mappings and the event needs to be mapped
        if (
            transport_type in self._EVENT_MAPPINGS
            and event_name in self._EVENT_MAPPINGS[transport_type]
        ):
            mapped_event = self._EVENT_MAPPINGS[transport_type][event_name]
        else:
            # Pass through the event name directly if no mapping exists
            mapped_event = event_name

        self._transport.event_handler(mapped_event)(handler)

    def event_handler(self, event_name: str) -> Callable[..., Any]:
        """Register an event handler.

        Args:
            event_name: The name of the event to handle. Common events:
                - "on_client_connected": Called when a client connects
                - "on_client_disconnected": Called when a client disconnects
                Other event names are passed through to the underlying transport.

        Returns:
            A decorator that registers the handler function.
        """

        def decorator(handler: Callable[..., Any]) -> Callable[..., Any]:
            if not hasattr(self, "_transport"):
                # Store the handler to be registered when the transport is created
                if event_name not in self._pending_handlers:
                    self._pending_handlers[event_name] = []
                self._pending_handlers[event_name].append(handler)
            else:
                self._register_handler(event_name, handler)
            return handler

        return decorator

    async def start(self, frame):
        """Start the transport."""
        await self._transport.start(frame)

    async def stop(self, frame):
        """Stop the transport."""
        await self._transport.stop(frame)

    async def cancel(self, frame):
        """Cancel the transport."""
        await self._transport.cancel(frame)

    @property
    def input(self):
        """Get the input transport."""
        return self._transport.input

    @property
    def output(self):
        """Get the output transport."""
        return self._transport.output
