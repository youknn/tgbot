@echo off
setlocal

REM Вставь сюда API ключ FreeModel
set "ANTHROPIC_AUTH_TOKEN=fe_oa_5d3beb4f2e0ff208a6987d9ddeb5a07f373873b133b9df1e"

set "ANTHROPIC_BASE_URL=https://cc.freemodel.dev"

REM Опционально: если gateway не любит experimental betas
set "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS=1"

set "ANTHROPIC_MODEL=claude-opus-4-7"

echo Starting Claude Code with Free Opus 4.7...
claude

endlocal