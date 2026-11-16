# Monitor Maid

A desktop session around a local [Ollama](https://ollama.com) model. Every turn is stored in SQLite. The model can ask the app to run a host command, and the default is that nothing runs until you approve it. Grok is a headless [Grok Build](https://docs.x.ai) session started with the `grok` command. The Grok window stays closed. The local model does not see that session.

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

Grok is optional. Install Grok Build so `grok` is on `PATH`, then sign in:

```bash
grok login
```

The app does not call the xAI HTTP API. A headless run can also use `XAI_API_KEY` in the environment, which the `grok` command reads itself. Choose the working directory, session, and model from the Grok menu. See [Grok Build sessions](#grok-build-sessions).

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

Scripts in `scripts/` use the git and `gh` logins already on this Mac. None of them stores a token.

```bash
scripts/build-deploy-local.sh
scripts/git-commit-push.sh "commit message"
scripts/tag-origin.sh ALPHA_1.0.2
scripts/release-and-build.sh ALPHA_1.0.2
```

`build-deploy-local.sh` builds `dist/Monitor Maid.app`, quits a running copy with a normal Quit, deletes `/Applications/Monitor Maid.app`, and copies the new bundle there. Pass `--no-smoke` to skip the offscreen launch check. The database, log, and `grok-build.json` stay in `~/Library/Application Support/MonitorMaid/`.

`git-commit-push.sh` stages, commits, and pushes the current branch. It does not force-push. `tag-origin.sh` creates one annotated tag and pushes that tag to origin. A name starting with `ALPHA_` starts the Windows and Linux workflow. `BETA_` does not. `release-and-build.sh` creates the GitHub release for a tag that is already on origin and starts that workflow on the tag, which is what attaches the zip and the tar.gz. If an `ALPHA_` push already started a run, the release script leaves it running. Pass `--again` to start a second one.

The database and log are created on first launch:

- macOS: `~/Library/Application Support/MonitorMaid/`
- Linux: `~/.local/share/monitor-maid/`
- Windows: `%APPDATA%\MonitorMaid\`

Set `MONITOR_MAID_DATA_DIR` to use a different folder.

## Sessions

Each session has a UUID. Turns are stored with an actor: `user`, `local_llm`, `grok`, or `system` (host results and notices).

The window opens with no local session. **New Local** asks for a working folder that already exists, and for the Ollama model. Host commands for that session run in the folder, and the local model is told that path. The folder you used last is the default. **New Grok** starts a new Grok Build session. **Reopen Grok** resumes one that already exists. Both keep the Grok window closed. After the last local session is deleted, the window stays empty until you start another.

**Model**, to the left of **Session**, lists the models Ollama has installed (`ollama list`). Choosing one unloads any other model Ollama has loaded and loads the one you picked. That choice is saved on the open session and becomes the default for a later new session. The first time the window opens, the list is filled but a model is not loaded until you pick a different one or send a message.

**Rename**, next to the session list, or **Rename session…** in the Session menu, sets a name you choose. That name stays. Until you rename it, a new session takes its name from the first message.

A new session started from a summary, a prompt, a question, an answer, or ticked history rows asks which folder and model to use, then puts that text in the input box. The chat stays empty. Edit the text if you want, then press Enter to send it. If you picked a different model, the one that was loaded is stopped and the new one is started.

**Enter** sends the message in the focused box. The left box goes to the local model. The right box goes to Grok. **⌘/Ctrl+Enter** adds a line. **⌘/Ctrl+Shift+Enter** in the Grok box sends both. The same chord in the local box does not send to Grok. **Enter sends**, under **Ask Local**, reverses Enter and ⌘/Ctrl+Enter for both boxes. The choice is saved.

The chat is on the left. History, Commands, and the two input boxes are on the right, with the boxes under the tabs. Drag the divider between the columns, and the handle between the local box and the Grok box.

**End session** asks with **Cancel**, **Yes**, and **No**. **Cancel** leaves the session open. **Yes** asks the local model for a short summary and stores it. A session with no turns is discarded. A very short one stores “No substantial discussion.” and does not call the model. Each call has a timeout (Settings, default 90s). If Ollama is down, the session still closes and the summary says why. **No** closes the session and does not store a summary. Notes already stored stay as they are.

Quitting the app runs that same shutdown for the session you have open. The summary dialog has **Skip summary** if you need to leave immediately.

If the process is killed, the next launch notices the unclean exit and offers to resume the last session, summarise it, or close it without a summary.

Closed sessions stay in the session list. **Reopen Local** continues one. Previous summaries are kept.

A prompt is stored only when the local model or Grok marks one in its reply:

```prompt
the prompt text
```

Ask either model to write a prompt. The app keeps the text inside that fence and marks the reply as a prompt. An ordinary answer is not stored as a prompt. **Create prompt** and **Create prompt with Grok** in the Session menu ask for one. Grok needs the `grok` command on `PATH`. A Grok prompt stays off the local model's record until you carry it into a new session. **Browse sessions…** lists every session and the notes stored on it. **Delete session** there removes that session’s transcript, summaries, and prompts after a confirmation. If a reply is still running, the open session cannot be deleted until it finishes. **View summary** opens the latest summary. **View prompt** opens the prompts stored on the session you have open. The History right-click menu opens the summary or prompt on that row. The note window has a button that starts a new session containing only that text.

**New session from checked items** (Session menu, or right-click a history row) reads the ticked rows from the database and places them in the input box of a new session. A kind filter does not drop a tick. Several rows stay in order, each with a label. A Grok row has a leading “Grok:” removed. Nothing is sent until you press Enter.

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

Host commands run as the user who opened the app. They receive that user's login-shell environment, including the `PATH` a Terminal window would have, so a Homebrew program such as `ollama` can be found when the app was opened from Finder. The command itself is still run by `/bin/sh`, so a zsh alias is not a command. macOS privacy access (Desktop, Documents, Downloads) is granted to each app separately; the system asks the first time a command needs one of those folders.

## Grok and the local model

**Ask Local**, under the left box, sends that box to the local model only. **Ask Grok**, under the right box, sends that box to the Grok Build session only. The local model is not part of an Ask Grok turn, and Grok is not part of an Ask Local turn. Checked history rows are included with Ask Grok when any are ticked. Text in the left box is not sent to Grok.

**Interact with Local**, under the Grok box, is the handoff. Grok is asked for one task in a `maid-to-local` fence. The local model sees that task and its own earlier chat, not Grok's private conversation. If it asks for host commands, the wrapper runs them. Grok then receives only the wrapper's report (what ran, the status, the exit code, and stdout or stderr), not the local model's other text. That repeats until Grok sends no task, or until Forward N round trips have run. The default is 4. Change N on the toolbar while it is running and the next round uses the new number. Command approval is still the Run control, separate from N.

The Grok box is always there. Type in it when you want Grok's view without adding that question to the local model's task. Enter in that box sends it to Grok. **⌘/Ctrl+Shift+Enter** there sends the local box and the Grok box.

- Each Grok turn is a headless `grok` process. The Grok window is not opened. The session keeps its own history, so a follow-up is sent with `--resume` and is not a copy of the local transcript.
- Grok turns are stored with “not visible to the local model”. They are filtered out again when the local prompt is built. The local system prompt never mentions Grok.
- **Send to both** runs the local model first, then Grok. Messages wait in a queue; a later send does not start until the earlier one has finished, so turns cannot interleave.
- A missing `grok` command, a sign-in error, and a non-zero exit are written in the status bar and as a host notice in the chat. **Retry** sends the failed half again. A refusal is shown and is not forwarded.

Interact with Local is how Grok assigns work. The task has to be a closed fence:

````
```maid-to-local
text that reads as a normal user message
```
````

An unclosed fence is not sent. A leading “Grok:” is removed. The local model is not told that another model wrote it. Forward N counts those round trips for the current click. It is not the host-command auto limit, and it does not use the lifetime forward counter.

Command review, when you press **Ask Grok to review** (or enable it for automatic runs), is a separate one-shot `grok -p` and does not resume the coding session. The reply must come back as `VERDICT: safe|unsafe|unsure`. `safe` does not bypass a block or the denylist. `unsafe` or a failed review drops an automatic run back to the dialog. The review is kept on the command row and in a Grok turn. It is not copied into the local prompt.

## History selection

The History tab lists questions, answers, summaries, prompts, and Grok notes, with a checkbox on each row. Filter by kind, then:

- **Ask Grok**, under the Grok box, sends that box plus any checked rows. The local model does not see that exchange.
- **Send to local** sends the checked rows as a user message. Grok notes are relabeled and are not introduced as Grok.
- **Interact with Local**, under the Grok box, asks the current Grok Build session for a task, runs host commands the local model requests, and returns the wrapper's results to Grok.
- Right-click a row to view that summary or prompt, or to start a new session from the ticked rows.

**Export** writes Markdown or plain text with the actor, timestamp, and content, including summaries, prompts, and the command log. If rows are checked, you can export just those. **Print** sends the same transcript to the printer.

## Settings

Ollama URL and model, the default execution and forward modes, N, command and summary timeouts, the character budget, and the denylist. Invalid regexes are rejected instead of saved. **Test Ollama** checks Ollama. **Test grok** runs `grok --version`. **Refresh models** fills the Ollama model list. If the name in the box is not installed, the first installed model is selected. Save to apply it to the session you have open. The Grok directory, session, and model are chosen from the Grok menu, not from this dialog.

## Grok Build sessions

The first **Ask Grok**, **New Grok**, **Reopen Grok**, or **Grok → Start session…** asks where to work and which session to use. Nothing is typed into a Grok window. **New Grok** starts a new session. **Reopen Grok** resumes one from the list.

1. Choose a directory. The field starts as the last directory you used, or the directory the app was started from. It has to exist.
2. The dialog runs `grok sessions list` in that directory and shows each session id, summary, status, and dates.
3. Resume a row, paste an id, or start a new session. A new session can take a UUID. That UUID is passed as `--session-id` and is only for a session that does not exist yet.
4. Type a prompt in the dialog, or leave it blank and type it in the Grok box.
5. Optionally pick a model (`grok models`), turn on `--always-approve`, choose plain or streaming-json output, and add extra flags such as `--max-turns 8`. Flags the app already sets (`-p`, `--cwd`, `--resume`, `--session-id`, `--output-format`) are rejected in that box.

**Ask Grok** sends the Grok box. streaming-json is the default, so tool calls and text show in the chat as they arrive. Stderr is shown when Grok reports an error. The note that says the session was found locally is left out. The command is:

```bash
grok --no-auto-update --cwd ~/Projects/monitor-maid \
  -p "Where is the host command runner?" \
  --output-format streaming-json \
  --resume 01a1ceed-ed80-78e2-8895-ddab61dc5e5b
```

A new named session looks like this:

```bash
grok --no-auto-update --cwd ~/Projects/monitor-maid \
  -p "Start from the README." \
  --session-id 33333333-3333-4333-8333-333333333333 \
  --output-format streaming-json
```

Add `--always-approve` when the checkbox is on, and `--model grok-4.7` when a model is chosen.

After a reply, the cursor returns to the input box. The next **Ask Grok** runs the same command with `--resume` and the new prompt.

**End session** shows that id when one was used, and writes `Grok Build session: <id>` into the transcript. The same id is stored in `grok-build.json` next to the database so a later launch can resume it:

- macOS: `~/Library/Application Support/MonitorMaid/grok-build.json`

Resume it from a terminal with:

```bash
grok --cwd ~/Projects/monitor-maid -p "Continue" \
  --resume 01a1ceed-ed80-78e2-8895-ddab61dc5e5b
```

**Grok → Export transcript…** runs `grok export <id> <file>`. The file must not already exist.

**Grok → Delete session…** asks, then runs `grok sessions delete <id>`.

A very long prompt is passed with `--prompt-file` instead of `-p`, because the command line has a size limit. The process is started with stdin closed, so grok cannot open its TUI. `grok` has to be on `PATH`. If it is missing, or the command is not signed in, the status bar and a host notice in the chat say so.

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

The tests cover the denylist, actor isolation, shutdown decisions, the instruction parser, export, the grok command builder, and a headless window close. They do not need a running Ollama or a signed-in grok. A fake `grok` binary covers the session list and streaming output.
