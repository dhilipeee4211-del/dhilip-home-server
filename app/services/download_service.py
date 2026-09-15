"""Persistent server-side cloud downloader.
All job state and file bytes live on the home server, never on Android.
"""
import os, time, threading, urllib.request, urllib.parse
from pathlib import Path
from werkzeug.utils import secure_filename
from app.database.database import get_db_connection
from app.utils.config import Config

class DownloadService:
    _executor = None
    _lock = threading.RLock()
    _controls = {}

    @classmethod
    def init(cls):
        from concurrent.futures import ThreadPoolExecutor
        if cls._executor is None:
            cls._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix='dhilip-download')
        # Jobs survive Android cache clearing and server process restart.
        with get_db_connection() as conn:
            rows = conn.execute("SELECT task_id FROM download_jobs WHERE status IN ('queued','downloading','pausing','paused')").fetchall()
        for r in rows:
            # A process restart cannot safely continue an open HTTP socket. Mark it paused;
            # the persisted .part file is retained and Resume uses HTTP Range when supported.
            cls._set(r['task_id'], status='paused')

    @classmethod
    def _set(cls, task_id, **changes):
        if not changes: return
        changes['updated_at'] = time.time()
        sets=', '.join(f'{k}=?' for k in changes)
        vals=list(changes.values())+[task_id]
        with get_db_connection() as conn:
            conn.execute(f'UPDATE download_jobs SET {sets} WHERE task_id=?', vals)

    @classmethod
    def _get(cls, task_id):
        with get_db_connection() as conn:
            row=conn.execute('SELECT * FROM download_jobs WHERE task_id=?',(task_id,)).fetchone()
            return dict(row) if row else None

    @classmethod
    def list(cls):
        with get_db_connection() as conn:
            return [dict(r) for r in conn.execute('SELECT * FROM download_jobs ORDER BY created_at DESC').fetchall()]

    @classmethod
    def create(cls, url, destination, filename):
        import uuid
        task_id='srvdl_'+uuid.uuid4().hex[:12]
        now=time.time()
        with get_db_connection() as conn:
            conn.execute('''INSERT INTO download_jobs(task_id,url,filename,destination,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?)''',
                         (task_id,url,filename,destination,'queued',now,now))
        cls._controls[task_id]=threading.Event()
        cls._executor.submit(cls._worker, task_id)
        return cls._get(task_id)

    @classmethod
    def control(cls, task_id, action):
        job=cls._get(task_id)
        if not job: return None
        if action=='pause':
            cls._controls.setdefault(task_id, threading.Event()).set()
            if job['status'] in ('queued','downloading'):
                cls._set(task_id,status='pausing')
        elif action=='resume':
            if job['status'] in ('paused','failed'):
                cls._controls[task_id]=threading.Event()
                cls._set(task_id,status='queued',error=None)
                cls._executor.submit(cls._worker, task_id)
        elif action=='cancel':
            cls._controls.setdefault(task_id, threading.Event()).set()
            cls._set(task_id,status='cancelled')
            from werkzeug.utils import secure_filename
            partial=Path(job['destination'])/(secure_filename(job['filename'])+'.part')
            partial.unlink(missing_ok=True)
        return cls._get(task_id)

    @classmethod
    def _worker(cls, task_id):
        job=cls._get(task_id)
        if not job: return
        control=cls._controls.setdefault(task_id, threading.Event())
        try:
            dest=Path(job['destination'])
            dest.mkdir(parents=True,exist_ok=True)
            target=dest/secure_filename(job['filename'])
            part=target.with_name(target.name+'.part')
            existing=part.stat().st_size if part.exists() else 0
            headers={'User-Agent':'DhilipHome-Server/1.0'}
            if existing:
                headers['Range']=f'bytes={existing}-'
            req=urllib.request.Request(job['url'],headers=headers)
            started=time.time(); last_bytes=existing; last_time=started
            with urllib.request.urlopen(req,timeout=30) as response:
                code=getattr(response,'status',200)
                # If the origin ignored Range, restart cleanly instead of corrupting the file.
                if existing and code != 206:
                    existing=0
                    part.unlink(missing_ok=True)
                    req=urllib.request.Request(job['url'],headers={'User-Agent':'DhilipHome-Server/1.0'})
                    response.close()
                    with urllib.request.urlopen(req,timeout=30) as response2:
                        return cls._stream(task_id,response2,part,target,0,control)
                total_header=response.headers.get('Content-Range')
                if total_header and '/' in total_header:
                    total=int(total_header.rsplit('/',1)[1])
                else:
                    total=int(response.headers.get('Content-Length') or 0)+existing
                cls._set(task_id,status='downloading',downloaded_bytes=existing,total_bytes=total)
                return cls._stream(task_id,response,part,target,existing,control)
        except Exception as e:
            if cls._get(task_id) and cls._get(task_id)['status'] not in ('cancelled','paused'):
                cls._set(task_id,status='failed',error=str(e))

    @classmethod
    def _stream(cls,task_id,response,part,target,offset,control):
        job=cls._get(task_id); total=int(response.headers.get('Content-Length') or 0)+offset
        mode='ab' if offset else 'wb'; downloaded=offset; last=downloaded; last_t=time.time()
        with open(part,mode) as out:
            while True:
                if control.is_set():
                    status=cls._get(task_id)['status']
                    if status=='cancelled':
                        out.flush(); return
                    out.flush(); os.fsync(out.fileno())
                    cls._set(task_id,status='paused',downloaded_bytes=downloaded,total_bytes=total)
                    return
                chunk=response.read(1024*1024)
                if not chunk: break
                out.write(chunk); downloaded+=len(chunk)
                now=time.time()
                if now-last_t>=0.5:
                    speed=(downloaded-last)/(now-last_t)
                    pct=int(downloaded*100/total) if total else 0
                    cls._set(task_id,status='downloading',progress_percent=min(100,pct),downloaded_bytes=downloaded,total_bytes=total,speed_bps=speed)
                    last,last_t=downloaded,now
            out.flush(); os.fsync(out.fileno())
        os.replace(part,target)
        cls._set(task_id,status='completed',progress_percent=100,downloaded_bytes=target.stat().st_size,total_bytes=target.stat().st_size,speed_bps=0,path=target.relative_to(Config.MEDIA_ROOT).as_posix())

    @classmethod
    def cleanup_cancelled(cls):
        # Cancelled jobs retain no partial bytes but remain in history.
        for job in cls.list():
            if job['status']=='cancelled':
                p=Path(job['destination'])/(secure_filename(job['filename'])+'.part')
                p.unlink(missing_ok=True)
