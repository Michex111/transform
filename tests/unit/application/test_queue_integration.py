import asyncio


def test_fake_queue_fetches_jobs_in_fifo_order(conversion_job_factory, fake_queue_port) -> None:
    first = conversion_job_factory(job_id="job-a")
    second = conversion_job_factory(job_id="job-b")

    asyncio.run(fake_queue_port.push_job(first))
    asyncio.run(fake_queue_port.push_job(second))

    first_message, first_job = asyncio.run(fake_queue_port.fetch_job())
    second_message, second_job = asyncio.run(fake_queue_port.fetch_job())

    assert first_message == "message-1"
    assert first_job.job_id == "job-a"
    assert second_message == "message-2"
    assert second_job.job_id == "job-b"


def test_fake_queue_acknowledge_records_message_id(fake_queue_port) -> None:
    asyncio.run(fake_queue_port.acknowledge_job("message-99"))

    assert fake_queue_port.acked_messages == ["message-99"]


def test_fake_queue_fail_records_message_and_error(fake_queue_port) -> None:
    asyncio.run(fake_queue_port.fail_job("message-3", "failure reason"))

    assert fake_queue_port.failed_messages == [("message-3", "failure reason")]


def test_fake_queue_returns_none_when_empty(fake_queue_port) -> None:
    assert asyncio.run(fake_queue_port.fetch_job()) is None