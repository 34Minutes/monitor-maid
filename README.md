# Monitor Maid

A desktop session around a local [Ollama](https://ollama.com) model. Every turn is stored in SQLite. The model can ask the app to run a host command, and the default is that nothing runs until you approve it. Grok (the xAI API) can watch the local transcript and review commands. The local model never sees Grok's messages.

## Setup

Python 3.11 or newer.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Install Ollama, start it, and pull a model:

```bash
ollama serve
ollama pull llama3.2
```

The default endpoint is `http://127.0.0.1:11434` and the default model is `llama3.2`. Change both in Settings.

Grok is optional. Create a key at [console.x.ai](https://console.x.ai) and either export it or paste it into Settings:

```bash
export XAI_API_KEY="your_api_key"
```

If `XAI_API_KEY` is set, it wins over the key stored in the app. The stored key lives in the local SQLite file (`chmod 600`). Calls use the Responses API at `https://api.x.ai/v1/responses` with `store: false`. The default model id is `grok-4.7` (the current text model on [docs.x.ai](https://docs.x.ai/developers/models)); change it in Settings if you want another one.

Run the app from a checkout:

```bash
python -m monitor_maid
```

Build a double-clickable app that does not need Python installed. The bundle contains the interpreter and the libraries this program imports (PyQt6 and httpx), not a full Python distribution. Ollama is still installed separately. Build on the kind of Mac that will run the app:

```bash
source .venv/bin/activate
pip install -r requirements-build.txt
pyinstaller --noconfirm --clean monitor-maid.spec
```

The result is `dist/Monitor Maid.app`. The first time you open it, use the Finder shortcut of control-click and then Open, because the app is not signed with an Apple certificate. Settings and the database stay in `~/Library/Application Support/MonitorMaid/`. Launched this way, host commands start in your home directory.

The same spec builds Windows and Linux. On those systems the program is `dist/monitor-maid/monitor-maid`. A tag named `ALPHA_*` builds both on GitHub and attaches the archives to that release. Windows is a `.zip`. Linux is a `.tar.gz`. Linux still needs the usual Qt libraries (`libxcb-cursor0`, `libxkbcommon0`, `libegl1`) on the machine that runs the app.

The database and log are created on first launch:

- macOS: `~/Library/Application Support/MonitorMaid/`
- Linux: `~/.local/share/monitor-maid/`
- Windows: `%APPDATA%\MonitorMaid\`

Set `MONITOR_MAID_DATA_DIR` to use a different folder.

## Sessions

Each session has a UUID. Turns are stored with an actor: `user`, `local_llm`, `grok`, or `system` (host results and notices).

**End session** asks the local model for a short summary and for the conclusions that were actually reached, then stores them. A session with no turns is discarded. A very short one stores “No substantial discussion.” and does not call the model. Each call has a timeout (Settings, default 90s). If Ollama is down, the session still closes and the summary says why.

Quitting the app runs that same shutdown for the session you have open. The summary dialog has **Skip summary** if you need to leave immediately.

If the process is killed, the next launch notices the unclean exit and offers to resume the last session, summarise it, or close it without a summary.

Closed sessions stay in the session list. **Reopen** continues one. Previous summaries are kept.

## Three execution modes

The local model requests a host action with an explicit block (see below). The toolbar mode is saved on the session and becomes the default for new sessions.

| Mode | Behaviour |
| --- | --- |
| Manual approval | Default. Every command opens a dialog. Nothing runs until you approve it. |
| Auto up to N, then manual | The first N safe commands run on their own (default N = 4). After that, approval is required again. The counter is per session. |
| Fully automatic | Safe commands run without a prompt. |

These still need a person, even in fully automatic mode:

- Anything on the denylist (recursive `rm`, `sudo`, `curl | sh`, shutdown, and the rest of the list in Settings).
- A command taken from a generic ` ```bash ` fence rather than a `maid-exec` block.
- A path outside the working directory, for file reads and writes.
- A Grok review that is missing, `unsafe`, or `unsure`, when “review before automatic execution” is on.

These never run, even if you press Approve:

- Deletes aimed at `/`, your home directory itself, or another top-level directory (`rm -rf /`, `rm -rf ~`, `rm -rf /usr`, …).
- Fork bombs, `mkfs`, raw writes to disk devices, `diskutil erase`.
- Writes under `/etc`, `/System`, `/bin`, `/sbin`, `/dev`, or `/private/etc`.
- Interactive programs (`vim`, `ssh`, a bare `python`, pagers). Stdin is closed, so they would hang. `python3 -c …` and `ssh -o BatchMode=yes` are allowed.
- `stop_process` for a pid this app did not start. The app checks the live command line before signalling, so a recycled pid is refused.

Non-zero exits and timeouts are stored on the command and shown in the transcript. The timed-out process is killed. Command output sent back to the local model does **not** include Grok’s review.

Every proposal is logged with the decision (`approved`, `rejected`, `auto`, or `blocked`), the exit code, and stdout/stderr. Open the Commands tab and double-click a row.

## Instruction convention

Tell the model what you want done in normal language. It is instructed to emit one JSON object per fence:

````
```maid-exec
{"op":"shell","command":"ls","cwd":"."}
```
````

| op | Fields |
| --- | --- |
| `shell` | `command`, optional `cwd` |
| `write_file` | `path`, `content` |
| `read_file` | `path` |
| `start_process` | `command`, optional `cwd` |
| `stop_process` | `pid` (only a process this app started) |

A one-line fallback also works: `MAID_EXEC: {"op":"shell","command":"pwd"}`.

A generic bash fence is treated as a guess and always asks you. An opened fence with no closing fence is not run. The app does not stitch a command across two replies; if the block is incomplete or ambiguous, you have to confirm a whole command.

`shell` uses the system shell. That is the documented interface, and it is why manual approval is the default. Do not turn on fully automatic mode for a model you do not trust.

## Monitor mode and Grok

**Monitor mode** shows a second input. Type your own question there when you want Grok's view without adding that question to the local model's task. You can send to the local model, to Grok, or to both. Checking rows and using **Ask Grok** is a different path: it sends only the items you marked, plus a question about those items.

- Grok is given the local transcript (quoted, so local replies are not treated as Grok’s own) plus the Grok-side conversation.
- Grok turns are stored with “not visible to the local model”. They are filtered out again when the local prompt is built. The local system prompt never mentions Grok.
- **Send to both** runs the local model first, then Grok, so Grok sees the new local reply. Messages wait in a queue; a later send does not start until the earlier one has finished, so turns cannot interleave.
- If the context is long, the oldest local turns are dropped and Grok is told they were omitted.
- Rate limits and connection errors retry a few times, then a notice is stored and **Retry** sends the failed half again. A read timeout is not retried automatically. A refusal is shown and is not forwarded.

Grok can suggest a follow-up for the local model only inside:

````
```maid-to-local
text that reads as a normal user message
```
````

Forwarding uses the same three modes (manual / auto up to N / fully automatic), with its own counter. A partial fence always asks. The text is inserted as a user message. A leading “Grok:” is removed. The local model is not told that another model wrote it.

Command review, when you press **Ask Grok to review** (or enable it for automatic runs), must come back as `VERDICT: safe|unsafe|unsure`. `safe` does not bypass a block or the denylist. `unsafe` or a failed review drops an automatic run back to the dialog. The review is kept on the command row and in a Grok turn. It is not copied into the local prompt.

## History selection

The History tab lists questions, answers, summaries, conclusions, and Grok notes, with a checkbox on each row. Filter by kind, then:

- **Ask Grok** sends the selection plus your question. The local model does not see that exchange.
- **Send to local** sends the selection as a user message. Grok notes are relabeled and are not introduced as Grok.
- **Verify with Grok** asks Grok whether the selected answers, summaries, and conclusions are supported by the transcript.

**Export** writes Markdown or plain text with the actor, timestamp, and content, including summaries, conclusions, and the command log. If rows are checked, you can export just those. **Print** sends the same transcript to the printer.

## Settings

Ollama URL and model, xAI key, model, and base URL, the default execution and forward modes, N, command and summary timeouts, the character budget for Grok’s copy of the transcript, and the denylist. Invalid regexes are rejected instead of saved. **Test Ollama** and **Test xAI** check the connection from the dialog. **Refresh models** fills the model list from `ollama`. If the name in the box is not installed, the first installed model is selected. Save to apply it to the session you have open.

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

The tests cover the denylist, actor isolation, shutdown decisions, the instruction parser, export, and a headless window close. They do not need a running Ollama or an API key.
