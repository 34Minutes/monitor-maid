from monitor_maid.instructions import parse_instructions, parse_relays, scrub_relay


def test_explicit_fence_and_prefix():
    text = """
Please list the directory.

```maid-exec
{"op":"shell","command":"ls -la","cwd":"."}
```

MAID_EXEC: {"op":"read_file","path":"notes.txt"}
"""
    found = parse_instructions(text)
    assert len(found) == 2
    assert found[0].op == "shell"
    assert found[0].command == "ls -la"
    assert found[0].ambiguous is False
    assert found[1].op == "read_file"
    assert found[1].path == "notes.txt"


def test_malformed_partial_and_bash_heuristic():
    malformed = parse_instructions("```maid-exec\n{nope}\n```")
    assert len(malformed) == 1
    assert malformed[0].malformed

    partial = parse_instructions("```maid-exec\n{\"op\":\"shell\",\"command\":\"pwd\"}\n")
    assert len(partial) == 1
    assert partial[0].partial

    both = parse_instructions("```bash\nprintf hi\n```\n```sh\npwd\n```")
    assert len(both) == 2
    assert all(item.ambiguous and item.op == "shell" for item in both)

    mixed = parse_instructions(
        "```maid-exec\n{\"op\":\"shell\",\"command\":\"pwd\"}\n```\n```bash\nrm -rf .\n```"
    )
    assert len(mixed) == 1
    assert mixed[0].command == "pwd"


def test_write_and_unknown_op():
    bad = parse_instructions('```maid-exec\n{"op":"format","command":"disk"}\n```')
    assert bad[0].malformed
    write = parse_instructions(
        '```maid-exec\n{"op":"write_file","path":"a.txt","content":"hi"}\n```'
    )
    assert write[0].op == "write_file"
    assert write[0].content == "hi"
    stop = parse_instructions('```maid-exec\n{"op":"stop_process","pid":42}\n```')
    assert stop[0].pid == 42


def test_relays_and_scrub():
    text = """
Here is a suggestion.

```maid-to-local
Check the port again.
```

```maid-to-local
second
"""
    blocks = parse_relays(text)
    assert len(blocks) == 2
    assert blocks[0].partial is False
    assert blocks[0].text == "Check the port again."
    assert blocks[1].partial is True
    assert scrub_relay("Grok: do the thing") == "do the thing"
    assert scrub_relay("As Grok, do the thing") == "do the thing"
