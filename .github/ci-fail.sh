#!/bin/bash
# Uso: ci-fail.sh "título" arquivo  -> mostra o essencial do log como anotação de erro do GitHub Actions
title="$1"; file="$2"
sel=$(grep -nE '^e: |error:|What went wrong|FAILED|Execution failed|Caused by|Unresolved|Could not|Error|Traceback|rror ' "$file" | head -30)
[ -z "$sel" ] && sel=$(tail -n 40 "$file")
msg=$(printf '%s\n--- fim do log ---\n%s' "$sel" "$(tail -n 15 "$file")" | head -c 3800)
msg="${msg//'%'/'%25'}"; msg="${msg//$'\r'/}"; msg="${msg//$'\n'/'%0A'}"
echo "::error title=$title::$msg"
