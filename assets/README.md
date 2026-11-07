# assets

This folder is a placeholder on `main`.

Every build writes a **new** `out_<job_id>.glb` to the orphan `assets` branch — never here — so `main`
history stays small and jsDelivr can serve the files with CORS:

```
https://cdn.jsdelivr.net/gh/<owner>/<repo>@assets/out_<job_id>.glb
```

Old outputs are kept forever; nothing is overwritten. Browse them on the `assets` branch.
