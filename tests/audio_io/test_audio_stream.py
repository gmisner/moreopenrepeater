import queue

import numpy as np

from audio_io.audio_stream import AudioBlockPump


def test_pump_forwards_input_to_queue():
    input_q: "queue.Queue[np.ndarray]" = queue.Queue(maxsize=4)
    output_q: "queue.Queue[np.ndarray]" = queue.Queue(maxsize=4)
    pump = AudioBlockPump(input_queue=input_q, output_queue=output_q, block_size=4)

    indata = np.array([[1.0], [2.0], [3.0], [4.0]], dtype=np.float32)
    outdata = np.zeros((4, 1), dtype=np.float32)

    pump.process(indata, outdata, frames=4, time=None, status=None)

    assert input_q.qsize() == 1
    np.testing.assert_array_equal(input_q.get_nowait(), indata)


def test_pump_drops_input_when_queue_full_instead_of_blocking():
    input_q: "queue.Queue[np.ndarray]" = queue.Queue(maxsize=1)
    output_q: "queue.Queue[np.ndarray]" = queue.Queue(maxsize=1)
    input_q.put_nowait(np.zeros((4, 1), dtype=np.float32))
    pump = AudioBlockPump(input_queue=input_q, output_queue=output_q, block_size=4)

    indata = np.ones((4, 1), dtype=np.float32)
    outdata = np.zeros((4, 1), dtype=np.float32)
    pump.process(indata, outdata, frames=4, time=None, status=None)

    assert pump.dropped_input_blocks == 1


def test_pump_outputs_silence_when_output_queue_empty():
    input_q: "queue.Queue[np.ndarray]" = queue.Queue(maxsize=4)
    output_q: "queue.Queue[np.ndarray]" = queue.Queue(maxsize=4)
    pump = AudioBlockPump(input_queue=input_q, output_queue=output_q, block_size=4)

    indata = np.zeros((4, 1), dtype=np.float32)
    outdata = np.full((4, 1), 9.0, dtype=np.float32)
    pump.process(indata, outdata, frames=4, time=None, status=None)

    np.testing.assert_array_equal(outdata, np.zeros((4, 1), dtype=np.float32))
    assert pump.starved_output_blocks == 1


def test_pump_writes_queued_output_block():
    input_q: "queue.Queue[np.ndarray]" = queue.Queue(maxsize=4)
    output_q: "queue.Queue[np.ndarray]" = queue.Queue(maxsize=4)
    queued_block = np.array([[5.0], [6.0], [7.0], [8.0]], dtype=np.float32)
    output_q.put_nowait(queued_block)
    pump = AudioBlockPump(input_queue=input_q, output_queue=output_q, block_size=4)

    indata = np.zeros((4, 1), dtype=np.float32)
    outdata = np.zeros((4, 1), dtype=np.float32)
    pump.process(indata, outdata, frames=4, time=None, status=None)

    np.testing.assert_array_equal(outdata, queued_block)


class Flags:
    """Stands in for sounddevice.CallbackFlags."""

    def __init__(self, input_overflow=False, output_underflow=False):
        self.input_overflow = input_overflow
        self.output_underflow = output_underflow

    def __bool__(self):
        return self.input_overflow or self.output_underflow


def test_pump_counts_the_sound_cards_own_overruns_and_underruns():
    pump = AudioBlockPump(input_queue=queue.Queue(maxsize=4), output_queue=queue.Queue(maxsize=4), block_size=4)
    block = np.zeros((4, 1), dtype=np.float32)

    pump.process(block, block.copy(), frames=4, time=None, status=Flags(input_overflow=True))
    pump.process(block, block.copy(), frames=4, time=None, status=Flags(output_underflow=True))
    pump.process(block, block.copy(), frames=4, time=None, status=Flags())

    assert (pump.input_overflows, pump.output_underflows) == (1, 1)
