import json, os, urllib.request, urllib.error
t = os.environ["GITHUB_TOKEN"]
body = json.dumps({"model": "openai/gpt-4o-mini", "messages": [{"role": "user", "content": "Скажи привет"}]}).encode()
variants = {
  "min": {"Authorization": f"Bearer {t}", "Content-Type": "application/json"},
  "gh-accept": {"Authorization": f"Bearer {t}", "Content-Type": "application/json",
                "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"},
  "ours": {"Authorization": f"Bearer {t}", "Content-Type": "application/json",
           "Accept": "application/json", "User-Agent": "taskcore"},
}
for url in ["https://models.github.ai/inference/chat/completions",
            "https://models.inference.ai.azure.com/chat/completions"]:
    for name, h in variants.items():
        req = urllib.request.Request(url, data=body, headers=h, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                print(url, name, r.status, r.geturl(), dict(r.headers).get("Content-Type"), r.read()[:200])
        except urllib.error.HTTPError as e:
            print(url, name, "HTTP", e.code, e.read()[:200])
        except Exception as e:
            print(url, name, "EXC", e)
