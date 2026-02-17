from agents.reply_writer.writer import write_reply


def test_reply_writer_length() -> None:
    reply = write_reply("Build a small repeatable system", max_chars=30)
    assert len(reply) <= 30
