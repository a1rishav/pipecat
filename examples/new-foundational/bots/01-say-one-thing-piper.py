#
# Copyright (c) 2024–2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

import os
import sys

import aiohttp
from dotenv import load_dotenv
from loguru import logger

from pipecat.frames.frames import EndFrame, TTSSpeakFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineTask
from pipecat.services.piper.tts import PiperTTSService
from pipecat.transports.base_transport import TransportParams
from pipecat.transports.network.small_webrtc import SmallWebRTCTransport
from pipecat.transports.network.webrtc_connection import SmallWebRTCConnection

load_dotenv(override=True)

logger.remove(0)
logger.add(sys.stderr, level="DEBUG")


async def run_bot(webrtc_connection: SmallWebRTCConnection):
    logger.info(f"Starting bot")

    # Create a transport using the WebRTC connection
    transport = SmallWebRTCTransport(
        webrtc_connection=webrtc_connection,
        params=TransportParams(
            audio_out_enabled=True,
        ),
    )

    # Create an HTTP session
    async with aiohttp.ClientSession() as session:
        tts = PiperTTSService(
            base_url=os.getenv("PIPER_BASE_URL"), aiohttp_session=session, sample_rate=24000
        )

        task = PipelineTask(Pipeline([tts, transport.output()]))

        # Register an event handler so we can play the audio when the client joins
        @transport.event_handler("on_client_connected")
        async def on_client_connected(transport, client):
            await task.queue_frames([TTSSpeakFrame(f"Hello there!"), EndFrame()])

        runner = PipelineRunner(handle_sigint=False)

        await runner.run(task)
