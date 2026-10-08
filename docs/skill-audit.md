# shop-tryon-skill phase-one audit

Audited from the checked-out `README.md`, `SKILL.md`, and Python imports.

## Runtime dependencies

The upstream repository has no `requirements.txt` or `pyproject.toml`. Its scripts
use these third-party packages:

- `openai` for Ark-compatible image generation and Qwen vision calls
- `oss2` for Alibaba Cloud OSS uploads
- `rembg` and `Pillow` for local background removal
- `onnxruntime` as the Windows inference runtime used by `rembg`

The remaining imports are from the Python standard library. This project's
LangChain dependency already installs `openai`; all other packages above are
declared directly in the root `pyproject.toml`.

## Upstream `scripts/.env.example` variables

- `JIMENG_ACCESS_KEY`
- `JIMENG_SECRET_KEY`
- `ARK_API_KEY`
- `ARK_BASE_URL`
- `ARK_IMAGE_MODEL`
- `ARK_VIDEO_MODEL_PRO`
- `ARK_VIDEO_MODEL_LITE`
- `ALIYUN_API_KEY`
- `OSS_ACCESS_KEY_ID`
- `OSS_ACCESS_KEY_SECRET`
- `OSS_BUCKET_NAME`
- `OSS_ENDPOINT`
- `OSS_PREFIX`
- `OSS_CDN_DOMAIN`
- `OPENAI_API_KEY`
- `OPENAI_BASE_URL`
- `ANTHROPIC_API_KEY`
- `FASHN_API_KEY`
- `REPLICATE_API_TOKEN`
- `REMOVEBG_API_KEY`
- `TRYON_OUTPUT_DIR`

The scripts additionally read `OSS_SIGN_EXPIRATION`, `DASHSCOPE_API_KEY`,
`DASHSCOPE_BASE_URL`, and `DASHSCOPE_MODEL`, although upstream omits these from
its example. The application root example includes them. The application uses
`ARK_ENDPOINT`; `sync_env.py` adds the `ARK_BASE_URL` alias required by upstream.

## Execution constraints confirmed

- The upstream `.env` must be placed in `vendor/shop-tryon-skill/scripts/`.
- The application must invoke the scripts as subprocesses with that directory as
  `cwd`; it must not import upstream modules.
- Set `PYTHONUTF8=1` for subprocesses on Windows because CLI output includes emoji
  that the default GBK console cannot encode.
