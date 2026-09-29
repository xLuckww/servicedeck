"""窗口网页可调用的 Python 接口（网页里用 window.pywebview.api.<方法名>(...) 调用，返回 Promise）。

规则：只调用 actions（以及同属 App 层的设置、打开文件等小工具）；返回值一律是可 JSON 序列化的 dict，
至少含 ok 字段；任何意外错误都转成中文提示，不把 Python 报错原文交给界面（详细原因写进 App 日志）。
pywebview 会把本类所有公开方法暴露给网页，所以内部属性一律以下划线开头。
"""

import functools
import logging

from servicedeck import actions
from servicedeck.app import osutil, settings

log = logging.getLogger(__name__)


def _safe(fn):
    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        try:
            r = fn(self, *args, **kwargs)
        except Exception as e:
            log.exception("界面调用 %s 出错", fn.__name__)
            return {"ok": False, "message": f"出了点问题，操作没有完成（{type(e).__name__}）。详细原因已写入 App 日志。",
                    "errors": [], "needs_confirm": False, "data": {}}
        if isinstance(r, actions.Result):
            d = r.to_dict()
            data = d.pop("data")
            return {**data, **d}  # 把 data 摊平，网页用起来简单
        return r
    return wrapper


def _flag(v) -> bool:
    return bool(v) and v != "false"


class Bridge:
    def __init__(self, window=None):
        self._window = window

    def _set_window(self, window) -> None:
        self._window = window

    # ---------- 状态 ----------

    @_safe
    def get_state(self):
        r = actions.overview().to_dict()
        state = {**r.pop("data"), "ok": r["ok"]}
        state["settings"] = settings.load()
        return state

    @_safe
    def list_unmanaged(self):
        return actions.list_unmanaged()

    @_safe
    def service_detail(self, service_id):
        return actions.service_detail(service_id)

    @_safe
    def logs(self, service_id, limit=200):
        return actions.service_logs(service_id, int(limit))

    # ---------- 启停 ----------

    @_safe
    def start(self, service_id, confirm=False):
        return actions.start(service_id, confirm=_flag(confirm))

    @_safe
    def stop(self, service_id, confirm=False):
        return actions.stop(service_id, confirm=_flag(confirm))

    @_safe
    def restart(self, service_id, confirm=False):
        return actions.restart(service_id, confirm=_flag(confirm))

    @_safe
    def start_all(self, confirm=False):
        return actions.start_all(confirm=_flag(confirm))

    @_safe
    def stop_all(self):
        return actions.stop_all()

    # ---------- 表单 ----------

    @_safe
    def template_catalog(self):
        return actions.template_catalog()

    @_safe
    def preview(self, template, inputs):
        return actions.preview(template, inputs or {})

    @_safe
    def template_choices(self, template, key, inputs):
        return actions.template_choices(template, key, inputs or {})

    @_safe
    def fix_permission(self, path):
        return actions.fix_script_permission(path)

    @_safe
    def draft_for_edit(self, service_id):
        return actions.draft_for_edit(service_id)

    @_safe
    def save_service(self, draft, service_id=None, confirm=False, force=False):
        return actions.save_service(draft, service_id=service_id or None, confirm=_flag(confirm), force=_flag(force))

    @_safe
    def delete_service(self, service_id, confirm=False):
        return actions.delete_service(service_id, confirm=_flag(confirm))

    @_safe
    def start_trial(self, draft):
        return actions.start_trial(draft)

    @_safe
    def trial_status(self, trial_id):
        return actions.trial_status(trial_id)

    @_safe
    def stop_trial(self, trial_id):
        return actions.stop_trial(trial_id)

    # ---------- 未纳入管理 ----------

    @_safe
    def adopt(self, pid):
        return actions.adopt(int(pid))

    @_safe
    def stop_unmanaged(self, pid, confirm=False, force=False):
        return actions.stop_unmanaged(int(pid), confirm=_flag(confirm), force=_flag(force))

    # ---------- 分组 ----------

    @_safe
    def add_group(self, name):
        return actions.add_group(name)

    @_safe
    def rename_group(self, group_id, name):
        return actions.rename_group(group_id, name)

    @_safe
    def reorder_groups(self, group_ids):
        return actions.reorder_groups(list(group_ids))

    @_safe
    def delete_group(self, group_id):
        return actions.delete_group(group_id)

    # ---------- 设置与备份 ----------

    @_safe
    def get_settings(self):
        return {"ok": True, **settings.load()}

    @_safe
    def set_setting(self, key, value):
        if key not in ("login_launch", "boot_autostart"):
            return {"ok": False, "message": f"未知的设置：{key}"}
        value = _flag(value)
        settings.update(**{key: value})
        if key == "login_launch":
            settings.apply_login_launch(value)
        return {"ok": True, **settings.load()}

    @_safe
    def set_collapsed(self, group_id, collapsed):
        c = dict(settings.load().get("collapsed") or {})
        c[group_id] = _flag(collapsed)
        settings.update(collapsed=c)
        return {"ok": True}

    @_safe
    def list_backups(self):
        return actions.list_backups()

    @_safe
    def restore_backup(self, index, confirm=False):
        return actions.restore_backup(int(index), confirm=_flag(confirm))

    # ---------- 打开文件 / 网页 / 选择文件 ----------

    @_safe
    def open_web(self, service_id):
        err = osutil.open_web(service_id)
        return {"ok": err is None, "message": err or ""}

    @_safe
    def open_log(self, service_id):
        err = osutil.open_log(service_id)
        return {"ok": err is None, "message": err or ""}

    @_safe
    def reveal(self, path):
        err = osutil.reveal(path)
        return {"ok": err is None, "message": err or ""}

    @_safe
    def pick_folder(self):
        return self._pick("folder")

    @_safe
    def pick_file(self):
        return self._pick("file")

    def _pick(self, kind: str) -> dict:
        import webview

        if self._window is None:
            return {"ok": False, "message": "窗口还没准备好"}
        dialog = webview.FileDialog.FOLDER if kind == "folder" else webview.FileDialog.OPEN
        result = self._window.create_file_dialog(dialog)
        if not result:
            return {"ok": False, "cancelled": True, "message": ""}
        path = result[0] if isinstance(result, (list, tuple)) else result
        return {"ok": True, "path": str(path)}
