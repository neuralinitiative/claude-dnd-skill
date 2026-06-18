# Campaign Arc — <campaign-name>

*Structured (imported) campaigns only. The full act/chapter tree lives here so it
stays out of the hot path at `/dm:dnd load`. `state.md → ## Campaign Arc` holds
only the lightweight pointer plus the current and next chapter. Read this file
when advancing chapters or when a player asks about the broader arc — not at
every load.*

*Dynamic and sandbox campaigns do not use this file; their arc lives inline in
`state.md → ## Campaign Arc`.*

```yaml
type: structured
source: "<source title>"
structure: linear        # linear | hub-and-spoke | faction-web
current_act: 1
current_chapter: "1.1"

acts:
  - act: 1
    title: "<act title>"
    chapters:
      - id: "1.1"
        title: "<chapter name>"
        location: "<primary location>"
        source_ref: "source/1.1.md"   # chapter text in the lazy corpus
        key_beats: ["<beat>", "<beat>"]
        telegraph_scene: "<setup scene that makes the beat feel earned>"
        branching_notes: "<how player choices can vary the chapter>"
        status: current               # current | complete | skipped | pending
      - id: "1.2"
        title: "<chapter name>"
        location: "<primary location>"
        source_ref: "source/1.2.md"
        key_beats: ["<beat>"]
        telegraph_scene: "<setup scene>"
        branching_notes: "<branching>"
        status: pending

outstanding_beats: ["<beat>", "<beat>"]

steering_notes: >
  <How to guide players toward outstanding beats without forcing — the world
  pressure to apply for the current chapter. Update at each /dm:dnd save when a
  beat advances or needs active steering.>

revision_log: []
```
