"""Run user-requested collection and login workers."""
import contextlib
import os
import sys

from _paths import APP_STATE_DIR
from storage import read_json
from task_store import get, update


def main(task_id=None):
    task_id=task_id or (sys.argv[1] if len(sys.argv)>1 else '')
    job=get(task_id)
    if not job:
        return 2
    log=APP_STATE_DIR/'logs'/f'{task_id}.log'
    try:
        log.parent.mkdir(parents=True,exist_ok=True)
        update(task_id,status='running',pid=os.getpid(),message='正在后台处理')
        with log.open('w',encoding='utf-8') as stream:
            with contextlib.redirect_stdout(stream),contextlib.redirect_stderr(stream):
                if job['kind']=='video_collect':
                    from video_collection_service import run
                    result=run(task_id)
                elif job['kind']=='collect':
                    from collection_service import run
                    result=run(task_id)
                elif job['kind']=='douyin_login':
                    from collection_service import run_login
                    result=run_login(task_id)
                else:
                    raise RuntimeError('未知任务类型')
        update(task_id,log=str(log))
        return 0 if result is not None else 1
    except OSError:
        update(task_id,status='failed',message='无法记录任务日志，请检查本机数据目录权限')
        return 1
    except Exception:
        update(task_id,status='failed',message='后台任务处理失败；请检查网络、浏览器和登录状态后重试',log=str(log))
        return 1


if __name__=='__main__':
    raise SystemExit(main())
