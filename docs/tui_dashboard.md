# Full-Screen Interactive TUI Dashboard (`agentcli tui`)

`agentcli tui` provides a terminal-native, full-screen interactive dashboard built on top of `prompt_toolkit`. It offers real-time visibility into conversation flows, sub-agent execution trees, token burn rates, and financial spend.

---

## 🚀 Launching the Dashboard

```bash
# Launch interactive TUI in current workspace
agentcli tui

# Launch with budget constraints and specific preset
agentcli tui --preset coding --budget medium --max-cost 0.50
```

---

## 🖥️ Layout & Pane Structure

The TUI is organized into a responsive, multi-pane layout:

```
┌──────────────────────────────────────────────────────────────────────────────┐
│  agentcli v2.0.0 | Model: auto | Preset: coding | Spend: $0.0124 (4,820 tok) │
├──────────────────────────────────────────────┬───────────────────────────────┤
│                                              │ ⚡ SUB-AGENTS & PEER SWARM    │
│  [18:04:12] USER:                            │   [file_ops] Created app.py   │
│  Refactor authentication routes              │   [shell] pytest passing (12) │
│                                              │   [code_analyzer] Clean       │
│  [18:04:15] ASSISTANT:                       ├───────────────────────────────┤
│  Updated JWT signing and token expiration    │ 📊 TELEMETRY & BUDGET GAUGE   │
│  middleware in auth/service.py.              │   Prompt:      3,200 tok      │
│                                              │   Completion:  1,620 tok      │
│                                              │   Cached:      1,150 tok      │
│                                              │   Budget: [████░░░░░░] 24.8%  │
├──────────────────────────────────────────────┴───────────────────────────────┤
│ prompt> /goal implement token refresh handler █                              │
├──────────────────────────────────────────────────────────────────────────────┤
│ Ready. [Tab] Focus | [Ctrl+O] Diffs | [Ctrl+H] History | [Ctrl+C] Exit       │
└──────────────────────────────────────────────────────────────────────────────┘
```

### Main Panes
1. **Header Banner**: Live package version, active model, active preset, and session spend.
2. **Conversation Stream**: Markdown-rendered chat history with user, assistant, and system messages.
3. **Sub-Agent Tree**: Live log of background tasks, peer delegations, and tool invocations.
4. **Telemetry & Budget Gauge**: Real-time token breakdown (prompt, completion, cached) and percentage bar of allocated USD budget.
5. **Interactive Input Buffer**: Full `prompt_toolkit` command line with slash command and `@file` auto-completion.
6. **Status Bar**: Current state, hotkey hints, and background execution status.

---

## ⌨️ Keybinding Reference

| Hotkey | Action | Description |
|---|---|---|
| `Tab` | **Cycle Focus** | Cycles input focus between Input Buffer, Chat Stream, Sub-Agents, and Metrics |
| `Ctrl + O` | **Toggle Diffs** | Opens modal inspecting modified file diffs generated during the session |
| `Ctrl + H` | **Toggle History** | Opens modal displaying full session conversation history |
| `Escape` | **Close Modals** | Closes any open modal inspection overlays |
| `Ctrl + C` / `Ctrl + D` | **Exit** | Gracefully disconnects and exits the TUI dashboard |
| `Enter` | **Submit** | Submits the query or slash command in the input buffer |

---

## 🎨 Modals & Inspection Overlays

### Step Diff Modal (`Ctrl+O`)
Displays git-style unified diffs of files modified by `file_ops` or autonomous sub-agents in the current session.

### Session History Modal (`Ctrl+H`)
Provides a scrollable list of all previous prompt turns and model responses for quick review without losing the current prompt draft.
