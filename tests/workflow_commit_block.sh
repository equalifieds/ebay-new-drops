git config user.name  "ebay-watcher-bot"
git config user.email "actions@github.com"
git add data/
if git diff --staged --quiet; then
  echo "No snapshot changes to commit."
  exit 0
fi
git commit -m "Update product snapshots ($(date -u +%Y-%m-%d\ %H:%M) UTC)"
# Someone may have pushed while we were scraping. If we lose that
# race the snapshot is thrown away and every product found this run
# is announced all over again on the next one, so rebase and retry.
for attempt in 1 2 3; do
  if git push; then
    echo "Pushed on attempt $attempt."
    exit 0
  fi
  echo "Push rejected; rebasing onto origin/main and retrying."
  git pull --rebase --autostash origin main || exit 1
  sleep 5
done
echo "Could not push the snapshot after 3 attempts." >&2
exit 1
