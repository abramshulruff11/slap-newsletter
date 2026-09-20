#!/usr/bin/env bash
#
# Run one pipeline stage so that it reports itself.
#
#   ci/run_stage.sh "Fetch content" 'python fetch_content.py'
#
# Why this exists: before SLA-52, a stage's outcome lived in the GitHub Actions
# UI and nowhere else. The consolidated daily email has to say WHICH stage broke
# and WHY, and the only place the "why" exists is that stage's own output — which
# no later step can read back out of the job log. So we tee it to a file, record
# the exit code into run_status.json, and re-raise the original code.
#
# Re-raising matters: this wrapper must be invisible to the workflow's own
# pass/fail. A critical stage still halts the job exactly as it did before; the
# only difference is that it leaves a record behind on its way out.
#
# The recorder is best-effort by design. If pipeline_status.py itself cannot run
# (a syntax error, a missing checkout), the stage's real exit code still governs
# — a broken reporter must never turn a green stage red.

set -u

name="$1"
shift
# Two call shapes, because the workflow has both. A one-liner passes the command
# as arguments; a multi-line stage pipes it in on a heredoc, which is the only
# form that survives arbitrary quoting (the 12:30 ET gate uses '+%H%M').
if [ "$#" -gt 0 ]; then
  cmd="$*"
else
  cmd=$(cat)
fi

slug=$(printf '%s' "$name" | tr '[:upper:]' '[:lower:]' | tr -c '[:alnum:]' '_' \
       | sed 's/__*/_/g; s/^_//; s/_$//')
mkdir -p ci_logs
log="ci_logs/${slug}.log"

start=$(date +%s)
# The pipe to tee means $? would be tee's status, so read the real one out of
# PIPESTATUS. (pipefail would also work, but PIPESTATUS is explicit about which
# side of the pipe we mean.)
bash -c "$cmd" 2>&1 | tee "$log"
code=${PIPESTATUS[0]}
elapsed=$(( $(date +%s) - start ))

python pipeline_status.py stage \
  --name "$name" --exit-code "$code" --seconds "$elapsed" --log "$log" \
  || echo "  [stage] warning: could not record '$name' (exit $code) to run_status.json"

exit "$code"
