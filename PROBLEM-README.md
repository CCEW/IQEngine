# Problem Statement: Modifying the IQEngine Query Recordings UI

## Context
We are attempting to modify the **Query Recordings UI** (the repository view, search filters, and recording table layout) within an active deployment of IQEngine running via Docker at `/opt/fast_project/iqengine/`. 

The objective is to update frontend visual elements or functional query components directly on the remote server (`lassena053528`) using an SSH connection in Visual Studio Code.

---

## The Problem
The human-readable source code (React, TypeScript, and TSX files) for the user interface **does not exist locally on the host machine** or as raw editable source files within the container. 

Instead, the UI layer has been **fully compiled, minified, and bundled into optimized production assets** baked directly into the pre-built Docker image (`ghcr.io/iqengine/iqengine:pre`) [399dda670b].

Inspecting the live container file structure reveals the layout engine's architecture:
```bash
cfrancia@lassena053528:/opt\$ docker exec -it iqengine ls -la /app/iqengine
total 12636
drwxr-xr-x 3 root root    4096 Dec 20  2025  .
drwxr-xr-x 1 root root    4096 Sep 10 01:26  ..
drwxr-xr-x 2 root root    4096 Dec 20  2025  assets
-rw-r--r-- 1 root root    2117 Dec 20  2025  index.html
... (static images and logos)
```

### Technical Barriers to Direct Editing
1. **Loss of Source Structure:** The native React component files (`Repository.tsx`, `SearchTile.tsx`, `RecordingRow.tsx`) have been crushed into single-line, highly dense JavaScript bundles inside the `assets/` directory.
2. **Code Minification:** All readable function names, variable scopes, and components have been obfuscated and rewritten into minified identifiers (e.g., `a()`, `t.b`) to reduce network transit overhead. Direct string manipulation or injection via VS Code is practically impossible.
3. **Impermanence:** Modifying static assets inside a running Docker file system is volatile. Any container restart, rebuild, or image pull (`docker compose down && docker compose up`) will completely wipe out the changes.
