"""Veil student demo: single-file API. Start with: uvicorn app:app --host 127.0.0.1"""
import io
import logging
import os
import secrets
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path

import cv2
import numpy as np
import torch
import torch.nn.functional as F
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from imwatermark import WatermarkDecoder, WatermarkEncoder
from PIL import Image, UnidentifiedImageError
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

LOG = logging.getLogger('veil')
ROOT = Path(__file__).resolve().parent
MAX_BYTES = 10 * 1024 * 1024
MAX_PIXELS = 16_000_000
Image.MAX_IMAGE_PIXELS = MAX_PIXELS
MODEL_ID = 'stable-diffusion-v1-5/stable-diffusion-v1-5'
STRENGTHS = {'light': 6 / 255, 'standard': 8 / 255, 'strong': 12 / 255}
TTL = 900
TOKEN = os.environ.get('VEIL_API_TOKEN', '')
if not TOKEN:
    raise RuntimeError('Set VEIL_API_TOKEN to a long random secret before starting Veil.')

# A single process and one worker protect the GPU; results stay in memory.
JOBS = {}
LOCK = threading.RLock()
POOL = ThreadPoolExecutor(max_workers=1)
STOP = threading.Event()
VAE = None

def cleanup_loop():
    while not STOP.wait(30):
        now = time.monotonic()
        with LOCK:
            for job_id, job in list(JOBS.items()):
                # Only expire finished jobs, starting the clock when work finishes.
                if job.get('finished') is not None and now - job['finished'] > TTL:
                    JOBS.pop(job_id, None)

@asynccontextmanager
async def lifespan(app):
    janitor = threading.Thread(target=cleanup_loop, daemon=True)
    janitor.start()
    yield
    STOP.set()
    POOL.shutdown(wait=False, cancel_futures=True)

app = FastAPI(title='Veil experimental demo', lifespan=lifespan)
app.mount('/css', StaticFiles(directory=ROOT / 'css'), name='css')
app.mount('/js', StaticFiles(directory=ROOT / 'js'), name='js')
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

@app.middleware('http')
async def headers(request, call_next):
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['Cache-Control'] = 'no-store'
    return response

def require_token(authorization: str = Header(default='')):
    if not secrets.compare_digest(authorization, 'Bearer ' + TOKEN):
        raise HTTPException(401, 'Incorrect access key')

def clean_image(data):
    if len(data) > MAX_BYTES:
        raise HTTPException(400, 'Image exceeds 10 MB')
    try:
        with Image.open(io.BytesIO(data)) as probe:
            if probe.format not in {'JPEG', 'PNG', 'WEBP'}:
                raise HTTPException(400, 'Use JPEG, PNG or WEBP')
            width, height = probe.size
            if width * height > MAX_PIXELS or min(width, height) < 256:
                raise HTTPException(400, 'Image dimensions are outside the allowed range')
            probe.verify()
        with Image.open(io.BytesIO(data)) as img:
            rgb = img.convert('RGB')
            # Rebuild from pixel bytes to remove embedded metadata.
            return Image.frombytes('RGB', rgb.size, rgb.tobytes())
    except HTTPException:
        raise
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        raise HTTPException(400, 'Invalid or unsupported image')

def get_vae():
    global VAE
    if VAE is None:
        from diffusers import AutoencoderKL
        device = 'cuda' if torch.cuda.is_available() else 'cpu'
        VAE = AutoencoderKL.from_pretrained(MODEL_ID, subfolder='vae', torch_dtype=torch.float32).to(device).eval()
        for p in VAE.parameters():
            p.requires_grad_(False)
    return VAE

def tensor(img, device):
    arr = np.asarray(img.convert('RGB'), dtype=np.float32) / 255
    return torch.from_numpy(arr).permute(2, 0, 1).unsqueeze(0).to(device)

def shield(img, epsilon):
    vae = get_vae()
    device = next(vae.parameters()).device
    width, height = img.size
    x = tensor(img.resize((512, 512), Image.Resampling.LANCZOS), device)
    with torch.no_grad():
        target = vae.encode(torch.zeros_like(x)).latent_dist.mean.detach()
    protected = x.detach().clone()
    for _ in range(180):
        protected.requires_grad_(True)
        loss = F.mse_loss(vae.encode(protected * 2 - 1).latent_dist.mean, target)
        grad = torch.autograd.grad(loss, protected)[0]
        with torch.no_grad():
            protected = (protected - grad.sign() / 255).clamp(0, 1)
            protected = torch.maximum(torch.minimum(protected, x + epsilon), x - epsilon).detach()
    delta = F.interpolate((protected - x).cpu(), size=(height, width), mode='bicubic', align_corners=False).clamp(-epsilon, epsilon)
    pixels = (tensor(img, 'cpu') + delta).clamp(0, 1).squeeze(0).permute(1, 2, 0).numpy()
    return Image.fromarray((pixels * 255).round().astype('uint8'))

def embed(img, ident):
    payload = bytes([1]) + ident.to_bytes(3, 'big')
    bgr = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
    encoder = WatermarkEncoder()
    encoder.set_watermark('bytes', payload)
    encoded = encoder.encode(bgr, 'dwtDctSvd')
    return Image.fromarray(cv2.cvtColor(encoded, cv2.COLOR_BGR2RGB))

def extract(img):
    bgr = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
    decoded = WatermarkDecoder('bytes', 32).decode(bgr, 'dwtDctSvd')
    if len(decoded) == 4 and decoded[0] == 1:
        return {'no_ai_edit': True, 'protection_id': int.from_bytes(decoded[1:], 'big')}
    return None

def worker(job_id, img, epsilon):
    with LOCK:
        JOBS[job_id]['status'] = 'processing'
    try:
        out = embed(shield(img, epsilon), secrets.randbelow(2**24))
        difference = np.abs(np.asarray(img).astype(np.int16) - np.asarray(out).astype(np.int16))
        data = io.BytesIO()
        out.save(data, format='PNG')
        with LOCK:
            JOBS[job_id].update(status='done', data=data.getvalue(), max_change=int(difference.max()), finished=time.monotonic())
    except Exception:
        LOG.exception('Veil job failed')
        with LOCK:
            JOBS[job_id].update(status='failed', finished=time.monotonic())

@app.get('/')
def index():
    return FileResponse(ROOT / 'index.html', media_type='text/html')

@app.get('/health')
def health():
    return {'ok': True, 'gpu': torch.cuda.is_available()}

@app.post('/protect', dependencies=[Depends(require_token)])
@limiter.limit('5/minute')
async def protect(request: Request, file: UploadFile = File(...), strength: str = Form('standard')):
    if strength not in STRENGTHS:
        raise HTTPException(400, 'Unknown strength')
    data = await file.read(MAX_BYTES + 1)
    img = clean_image(data)
    with LOCK:
        if sum(j['status'] in ('queued', 'processing') for j in JOBS.values()) >= 5:
            raise HTTPException(503, 'Server busy; please try again later')
        job_id = uuid.uuid4().hex
        JOBS[job_id] = {'status': 'queued', 'finished': None}
        POOL.submit(worker, job_id, img, STRENGTHS[strength])
    return {'job_id': job_id}

@app.get('/jobs/{job_id}', dependencies=[Depends(require_token)])
def status(job_id: str):
    with LOCK:
        job = JOBS.get(job_id)
        if not job:
            raise HTTPException(404, 'Job not found or expired')
        return {'status': job['status'], 'max_change': job.get('max_change')}

@app.get('/download/{job_id}', dependencies=[Depends(require_token)])
def download(job_id: str):
    with LOCK:
        job = JOBS.get(job_id)
        if not job or job['status'] != 'done':
            raise HTTPException(404, 'Not ready or expired')
        data = job['data']
    return Response(data, media_type='image/png', headers={'Content-Disposition': 'attachment; filename="veil_protected.png"'})

@app.post('/verify')
@limiter.limit('20/minute')
async def verify(request: Request, file: UploadFile = File(...)):
    img = clean_image(await file.read(MAX_BYTES + 1))
    try:
        found = extract(img)
    except Exception:
        LOG.exception('Seal reading failed')
        raise HTTPException(422, 'Could not read this image')
    return {'veil_watermark_found': found is not None, 'details': found,
            'note': 'The seal is unauthenticated. A missing seal does not prove absence of prior processing.'}
