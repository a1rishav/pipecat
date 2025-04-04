#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#
import os
import sys

from dotenv import load_dotenv
from loguru import logger

from pipecat.frames.frames import (
    InputAudioRawFrame,
    InputImageRawFrame,
    OutputAudioRawFrame,
    OutputImageRawFrame,
)
from pipecat.pipeline.parallel_pipeline import ParallelPipeline
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.frame_processor import Frame, FrameDirection, FrameProcessor
from pipecat.transports.base_transport import TransportParams
from pipecat.transports.network.small_webrtc import SmallWebRTCTransport
from pipecat.transports.services.daily import DailyParams, DailyTransport

load_dotenv(override=True)

logger.remove(0)
logger.add(sys.stderr, level="DEBUG")


class MirrorProcessor(FrameProcessor):
    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, InputAudioRawFrame):
            await self.push_frame(
                OutputAudioRawFrame(
                    audio=frame.audio,
                    sample_rate=frame.sample_rate,
                    num_channels=frame.num_channels,
                )
            )
        elif isinstance(frame, InputImageRawFrame):
            await self.push_frame(
                OutputImageRawFrame(image=frame.image, size=frame.size, format=frame.format)
            )
        else:
            await self.push_frame(frame, direction)


async def run_bot(webrtc_connection):
    pipecat_transport = SmallWebRTCTransport(
        webrtc_connection=webrtc_connection,
        params=TransportParams(
            camera_in_enabled=True,
            camera_out_enabled=True,
            camera_out_is_live=True,
            audio_in_enabled=True,
            audio_out_enabled=True,
            vad_enabled=False,
        ),
    )

    room_url = "https://filipi.daily.co/public"
    daily_transport = DailyTransport(
        room_url,
        None,
        "SmallWebRTC",
        params=DailyParams(
            camera_in_enabled=True,
            camera_out_enabled=True,
            camera_out_is_live=True,
            audio_in_enabled=True,
            audio_out_enabled=True,
            vad_enabled=False,
        ),
    )

    pipeline = Pipeline(
        [
            ParallelPipeline(
                [
                    daily_transport.input(),
                    MirrorProcessor(),
                    pipecat_transport.output(),
                ],
                [
                    pipecat_transport.input(),
                    MirrorProcessor(),
                    daily_transport.output(),
                ],
            )
        ]
    )

    task = PipelineTask(
        pipeline,
        # TODO: I believe we don't need this
        params=PipelineParams(
            allow_interruptions=False,
        ),
    )

    @pipecat_transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        logger.info("Pipecat Client connected")

    @pipecat_transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        logger.info("Pipecat Client disconnected")

    @pipecat_transport.event_handler("on_client_closed")
    async def on_client_closed(transport, client):
        # TODO ???
        logger.info("Pipecat Client closed")
        await task.cancel()

    runner = PipelineRunner(handle_sigint=False)

    await runner.run(task)
