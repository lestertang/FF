Changes
=======

Bug-fix pass over the FaceFusion tree: 27 defects found by review and fixed, each
reproduced before the fix and verified after.

Scope: `facefusion/` core, workflow, job and I/O modules. No feature work, no
refactors, no dependency changes.


Repository context
------------------

This tree is **not** pristine upstream `facefusion/master`. Before this pass, it
already carried a local patch across 22 files that removes the `content_analyser`
module and its call sites. That patch is pre-existing, was not reviewed or
modified here, and is unrelated to everything below. It matters for two reasons:

- Diffing this tree against upstream shows those 22 files as changed on top of the
  29 listed here.
- `tests/test_inference_manager.py::test_get_inference_pool` fails because of it
  (see *Known remaining issues*). That failure is not a regression from this pass.


Verification
------------

Run from the repository root with `ffmpeg` and `ffprobe` on `PATH`:

```
flake8 facefusion.py install.py && flake8 facefusion tests
mypy facefusion.py install.py && mypy facefusion tests
pytest tests
```

Results at the end of this pass:

| Check | Before | After |
| --- | --- | --- |
| flake8 (project config, incl. import order) | clean | clean |
| mypy | 1 error | 1 error (pre-existing, unrelated — see below) |
| pyflakes | clean | clean |
| pytest | 98 passed, 132 blocked | **168 passed, 1 failed** |

The 132 previously-blocked tests could not run because a pre-existing deadlock
(H6 below) hung the suite indefinitely. Excluded from the run: `tests/test_cli_*`
(needs multi-GB processor model downloads) and `test_merge_video` (needs NVENC/AMF
hardware — ffmpeg advertises those encoders regardless of whether a GPU is present).


High severity
-------------

### H1 — `download.py`: infinite busy-loop on any failed model download

`conditional_download` spawned curl via `Popen`, never waited on it, never checked
its exit status, and spun on `while current_size < download_size` with no sleep. A
dropped connection, 403, truncated response or full disk left it pinning a core
forever behind a frozen progress bar.

Now polls `process.poll()`, sleeps between checks, calls `communicate()`, does a
final progress update and logs `downloading_failed` when the file came up short.
A missing/zero `Content-Length` is now an explicit error instead of a silent skip.

Reproduced with a local server advertising `Content-Length: 10000000` then dropping
the GET: **original exits 124 (still spinning after 40 s); patched returns in 0.5 s**
with the error logged.

New locale key: `downloading_failed` in `locales.py`.

### H2 — `audio.py:69`: `int16` overflow corrupts long audio

Spectrogram frame indices were cast to `int16` (max 32767). Now `int32`.

| audio length | spectrogram frames | max index needed | after old cast | negative indices |
| --- | --- | --- | --- | --- |
| 120 s | 28 801 | 28 800 | 28 800 | 0 |
| 150 s | 36 001 | 36 000 | 32 764 | 1 011 |
| 300 s | 72 001 | 72 000 | 32 764 | 10 240 |

Overflow began at ~410 s for `get_voice_frame` (16 kHz, what `lip_syncer` uses) and
~137 s for `get_audio_frame` (48 kHz). Past that, indices wrapped negative, were
dropped by the `>= audio_step_size` filter, and the remainder saturated — so the
tail of the audio mapped to wrong or unreachable mel windows.

### H3 — `exit_helper.py:28`: the graceful-shutdown wait was dead code

`process_manager.stop()` sets state to `'stopping'`, which makes `is_processing()`
false, so `while process_manager.is_processing()` never executed. Ctrl+C deleted the
temp directory while non-daemon `ThreadPoolExecutor` workers were still writing into
it. Changed to `while not process_manager.is_pending()`. Same idiom, same fix, at
`uis/components/instant_runner.py:106` (Clear pressed right after Stop).

### H4 — `inference_manager.py`: stale `@lru_cache` ignored execution-provider changes

`resolve_static_inference_providers` is cached on `(module_name, execution_device_id)`
but reads `execution_providers` from state internally. `cache_clear()` was called
only in the test suite. Switching CPU → CUDA in the UI dropped the sessions but
rebuilt them from the stale provider list, silently continuing on CPU.

`clear_inference_pool()` now calls `resolve_static_inference_providers.cache_clear()`.

```
1st call (providers = cpu):    ['CPUExecutionProvider']
state now says:                ['cuda']
2nd call (providers = cuda):   ['CPUExecutionProvider']   <- stale, before fix
after cache_clear():           [('CUDAExecutionProvider', {'device_id': 0, ...})]
```

### H5 — CWD-relative module discovery made the app unusable outside the repo root

Seven sites scanned `'facefusion/processors/modules'` and `'facefusion/uis/layouts'`
relative to the *current working directory*, while every model path correctly used
`resolve_relative_path()`. Run from anywhere else, both lists came back empty, which
became `choices=[]` in argparse — so `--processors face_swapper` failed with
`invalid choice: 'face_swapper' (choose from )`, and `force-download` downloaded
nothing while returning exit code 0.

Fixed at `args.py:66`, `core.py:112`, `program.py:214`, `program.py:225`,
`uis/components/download.py:38`, `uis/components/execution.py:38`,
`uis/components/processors.py:42`.

### H6 — `ffmpeg.py`: `get_available_encoder_set` deadlock (found while validating)

`ffmpeg -encoders` writes 14 281 bytes to stdout, but `run_ffmpeg` never drained it
while polling `process.wait()`. Any call made while `process_manager.is_processing()`
filled the pipe buffer and hung forever. This is what stalled the test suite for
50+ minutes with ffmpeg sitting at 0.1 s CPU.

Now uses `open_ffmpeg` + `communicate()`, which drains both pipes concurrently and
no longer depends on process state at all. Verified in `pending`, `processing` and
`stopping` states, including `log_level=debug`.

Pre-existing upstream (`run_ffmpeg` and `get_available_encoder_set` were byte-identical
to upstream before this change). CI never hit it because `tests/test_ffmpeg.py:90`
stubs the function to `['aac']`/`['libx264']` when `CI` is set.


Medium severity
---------------

| Location | Fix |
| --- | --- |
| `ffprobe.py` | `duration`, `bit_rate`, `width`, `height` now parse through new `extract_entry_float` / `extract_entry_int` helpers (built on the existing `cast_*` idiom). Missing keys and ffprobe's common `N/A` no longer raise `TypeError`/`ValueError`. |
| `audio.py` | `read_audio` / `read_voice` return `None` when ffmpeg fails, instead of `numpy.frombuffer(None)`. `get_audio_frame` / `get_voice_frame` guard the `None`. |
| `audio.py` | `prepare_audio` guards divide-by-zero on a fully silent track (previously produced NaN). |
| `download.py` | `process_manager.end()` is now unconditional, so a failed download can't wedge the state at `'checking'` — which would deadlock `get_inference_pool`'s `while is_checking()` wait. |
| `temp_helper.py` | Temp directories are now `<name>-<crc32-of-abspath>`, so same-named targets in different directories stop colliding under `batch-run`. |
| `temp_helper.py` | `resolve_temp_frame_set` uses `cast_int` and skips non-numeric filenames instead of raising `ValueError`. |
| `face_store.py` + `hash_helper.py` | Frame keys moved from crc32 to a new `create_wide_hash` (crc32 + adler32, 64-bit). Cost measured at **0.56 ms vs 0.48 ms per 1080p frame**; collision probability over 65 k frames drops from **39% to 1.2e-10**. The crc32 `create_hash` used for model-file validation is deliberately unchanged. |
| `ffmpeg.py:119` | `splitlines()` instead of `split(os.linesep)` — ffmpeg emits `\n`, so on Windows the whole stderr was logged as one line. |
| `ffmpeg.py:315` | Writes `'\n'` instead of `os.linesep` (text mode was double-translating to `\r\r\n`), and references `concat_video_path` instead of a closed file handle's `.name`. |
| `video_manager.py:117` | Writer pool keyed on all its parameters, not just `video_path`, so a cache hit can't return a writer built with different fps/resolution. |


Low severity
------------

| Location | Fix |
| --- | --- |
| `vision.py:68,194` | `restrict_image_resolution` / `restrict_video_resolution` compared resolutions as tuples, which is lexicographic — the second dimension was ignored whenever the first decided. Now compares per-dimension. Current call sites pass uniformly-scaled source resolutions so this was latent, but it returned resolutions exceeding the source for any other caller. |
| `face_creator.py:121` | `refill_faces` used `faces[anchor_index_previous]` with `anchor_index_previous = -1`, which indexes the **last** element rather than meaning "no anchor". Leading `None`s raised `AttributeError: 'NoneType' object has no attribute 'landmark_set'`; they now hold the first real face. |
| `face_tracker.py:31,49` | `select_face_track` takes `frame_index` and skips tracks already holding that frame, so two faces in one frame can no longer collapse into one track (the second silently overwrote the first). |
| `face_selector.py:73` | `source_faces and A == 'auto' or B == 'auto'` parsed as `(source_faces and A) or B`. Parenthesised to match intent. |
| `vision.py` | Deleted `blend_vision_frames`, an exact unused duplicate of `blend_frame`. |
| `vision.py:350` | `merge_tile_frames` derived `tiles_per_row` as `pad_width // tile_width`, which disagrees with the column count `create_tile_frames` produces whenever `4*size[2] >= size[0]`. Now uses the same `range(...)` expression. Round-trip is bit-exact for all 19 configured model sizes **and** the previously-broken `(64,8,16)` shape. |
| `jobs/job_runner.py:110` | Calls `job_helper.get_step_output_path` directly instead of relying on a re-export in `job_manager`. |
| `jobs/job_manager.py:132,146,162` | `step_index = -2` silently mapped to the last step. Now proper Python-style negative indexing. |
| `config.py:9` | `@lru_cache` keyed on nothing while reading `config_path` from state. Split into `get_static_config_parser()` + a cached `create_static_config_parser(config_path)`. |
| `hash_helper.py:22` | `.strip()` on hash-file contents, so a trailing newline no longer fails validation. |
| `ffmpeg.py` | `fix_audio_encoder` never remapped `libvorbis`, which MP4/MOV cannot mux — `--output-audio-encoder libvorbis` silently failed for those containers. Found while validating; pre-existing upstream, masked by the same CI stub as H6, which only ever exercises `aac`. |


Known remaining issues
----------------------

Not fixed. Each was investigated and deliberately left alone.

1. **`tests/test_inference_manager.py::test_get_inference_pool` fails.** It requests
   `model_names = ['fairface']`, but `face_classifier`'s source set is keyed
   `'face_classifier'`, and `create_inference_pool` builds pool keys from the source
   set — not from `model_names`. Introduced by the pre-existing local patch described
   under *Repository context*, so fixing it belongs with that patch, not here.

2. **`mypy` reports 1 error:** `facefusion/face_masker.py:237`, assigning a float64
   expression to a variable inferred as float32. The file is byte-identical to
   upstream. Left alone because the only safe-looking fixes either change the runtime
   dtype of a mask (risking downstream numeric changes) or add churn to an untouched
   file, and the error may be specific to the mypy/numpy versions used here.

3. **`run_ffmpeg` returns `returncode = None` when called outside `processing` state**,
   so callers checking `returncode == 0` get `False`. Every production caller runs
   inside `process_manager.start()`, so it is not user-reachable, and changing the
   loop would alter stop-on-request semantics. H6 was the one caller that hit it.

4. **`run_ffmpeg_with_progress` never drains stderr** while reading stdout. The same
   deadlock class as H6. Currently harmless because `-loglevel error` keeps stderr
   small, but a command emitting >64 KB of warnings would hang.

5. **`get_available_encoder_set` reports build-time encoders, not usable ones.**
   ffmpeg lists `h264_nvenc`, `h264_amf`, `hevc_nvenc`, `hevc_amf` even with no
   matching GPU, so those appear as valid `--output-video-encoder` choices and then
   fail at encode time. This is what makes `test_merge_video` fail on GPU-less machines.

6. **`FACE_STORE` never evicts.** H-severity collision risk is fixed, but the store
   still grows for the length of a run. Unchanged — bounding it is a design decision,
   not a bug fix.


Files changed
-------------

25 source files, 4 test files.

```
facefusion/args.py                          facefusion/jobs/job_manager.py
facefusion/audio.py                         facefusion/jobs/job_runner.py
facefusion/config.py                        facefusion/locales.py
facefusion/core.py                          facefusion/program.py
facefusion/download.py                      facefusion/temp_helper.py
facefusion/exit_helper.py                   facefusion/uis/components/download.py
facefusion/face_creator.py                  facefusion/uis/components/execution.py
facefusion/face_selector.py                 facefusion/uis/components/instant_runner.py
facefusion/face_store.py                    facefusion/uis/components/processors.py
facefusion/face_tracker.py                  facefusion/video_manager.py
facefusion/ffmpeg.py                        facefusion/vision.py
facefusion/ffprobe.py                       facefusion/inference_manager.py
facefusion/hash_helper.py

tests/test_face_creator.py                  tests/test_ffmpeg.py
tests/test_face_tracker.py                  tests/test_temp_helper.py
```

Test changes are assertion updates for changed signatures/layout plus new regression
coverage for `refill_faces` leading-`None`s, same-frame track collisions, temp-directory
uniqueness, and the `libvorbis` encoder remap.
