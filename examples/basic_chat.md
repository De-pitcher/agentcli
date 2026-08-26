# Example: basic chat with file context

```bash
export OPENROUTER_API_KEY=sk-or-...
agentcli config init
agentcli chat
```

```
agentcli — model: meta-llama/llama-3.1-8b-instruct:free  (Ctrl+C or /exit to quit)

you> what does this function do? @src/parser.py
assistant> This function tokenizes...

you> /exit
```

## Switching models per-session

```bash
agentcli chat --model qwen/qwen-2.5-72b-instruct:free
```

## Preloading multiple files

```bash
agentcli chat --file src/main.py --file src/config.py
```
