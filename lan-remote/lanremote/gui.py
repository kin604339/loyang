"""Small Tk status window shown on the host computer (AnyDesk-style 'this desk' panel)."""
from __future__ import annotations

import asyncio
import time
import tkinter as tk
import webbrowser
from tkinter import messagebox, ttk

from . import __version__
from .agent import launch_viewer, new_pin, save_config
from .netutil import local_ips


def show_error(msg: str):
    root = tk.Tk()
    root.withdraw()
    messagebox.showerror("LAN Remote", msg)
    root.destroy()


def show_info(msg: str):
    root = tk.Tk()
    root.withdraw()
    messagebox.showinfo("LAN Remote", msg)
    root.destroy()


def run(server, loop: asyncio.AbstractEventLoop):
    root = tk.Tk()
    root.title(f"LAN Remote v{__version__}")
    root.geometry("520x560")
    root.minsize(460, 480)
    try:
        root.tk.call("tk", "scaling", 1.25)
    except Exception:
        pass

    style = ttk.Style()
    try:
        style.theme_use("clam")
    except Exception:
        pass

    # ---- approval dialog (login with require_accept) ---------------------
    async def approval(name: str, ip: str) -> bool:
        fut = loop.create_future()

        def ask():
            ok = messagebox.askyesno("連線請求", f"{name} ({ip}) 想要連線並控制這台電腦。\n\n允許嗎？", parent=root)
            loop.call_soon_threadsafe(fut.set_result, ok)

        root.after(0, ask)
        return await fut

    server.approval_handler = approval

    events: list[str] = []

    def on_event(kind, entry):
        if kind in ("login", "login_failed", "login_denied", "viewer_connected", "viewer_disconnected", "upload",
                    "download", "power", "shell", "chat"):
            desc = {k: v for k, v in entry.items() if k not in ("time", "kind")}
            events.append(f"{entry['time'][11:]} {kind} {desc}")
            del events[:-100]

    server.on_event = on_event

    # ---- layout ----------------------------------------------------------
    pad = {"padx": 10, "pady": 4}
    top = ttk.LabelFrame(root, text="這台電腦（被控端）— 把網址和 PIN 給對方")
    top.pack(fill="x", **pad)

    ttk.Label(top, text="名稱:").grid(row=0, column=0, sticky="w", padx=6, pady=3)
    name_var = tk.StringVar(value=server.name)
    name_entry = ttk.Entry(top, textvariable=name_var, width=28)
    name_entry.grid(row=0, column=1, sticky="w", padx=6)

    def save_name():
        server.settings["name"] = name_var.get().strip() or server.name
        save_config(server.settings)
        root.title(f"LAN Remote - {server.name}")

    ttk.Button(top, text="儲存", command=save_name, width=6).grid(row=0, column=2, padx=4)

    ttk.Label(top, text="連線網址:").grid(row=1, column=0, sticky="nw", padx=6, pady=3)
    url_text = tk.Text(top, height=2, width=34, relief="flat", bg=root.cget("bg"))
    url_text.grid(row=1, column=1, columnspan=2, sticky="w", padx=6)

    ttk.Label(top, text="PIN 碼:").grid(row=2, column=0, sticky="w", padx=6, pady=3)
    pin_var = tk.StringVar(value=server.settings["pin"])
    pin_lbl = ttk.Label(top, textvariable=pin_var, font=("Consolas", 20, "bold"))
    pin_lbl.grid(row=2, column=1, sticky="w", padx=6)

    def regen_pin():
        server.settings["pin"] = new_pin()
        save_config(server.settings)
        pin_var.set(server.settings["pin"])
        server.kick_all()

    def set_pin():
        win = tk.Toplevel(root)
        win.title("設定 PIN 碼")
        ttk.Label(win, text="新的 PIN 碼（4~12 碼）:").pack(padx=12, pady=6)
        v = tk.StringVar()
        e = ttk.Entry(win, textvariable=v, width=20)
        e.pack(padx=12, pady=4)
        e.focus()

        def ok():
            p = v.get().strip()
            if 4 <= len(p) <= 12:
                server.settings["pin"] = p
                save_config(server.settings)
                pin_var.set(p)
                server.kick_all()
                win.destroy()

        ttk.Button(win, text="確定", command=ok).pack(pady=6)
        win.bind("<Return>", lambda _: ok())

    btns = ttk.Frame(top)
    btns.grid(row=2, column=2, padx=4)
    ttk.Button(btns, text="重新產生", command=regen_pin, width=8).pack(side="left")
    ttk.Button(btns, text="自訂", command=set_pin, width=5).pack(side="left", padx=2)

    # ---- options ---------------------------------------------------------
    opts = ttk.LabelFrame(root, text="安全性 / 選項")
    opts.pack(fill="x", **pad)
    accept_var = tk.BooleanVar(value=bool(server.settings.get("require_accept")))
    view_var = tk.BooleanVar(value=bool(server.settings.get("view_only")))
    shell_var = tk.BooleanVar(value=bool(server.settings.get("allow_shell")))

    def opt_changed():
        server.settings["require_accept"] = accept_var.get()
        server.settings["view_only"] = view_var.get()
        server.settings["allow_shell"] = shell_var.get()
        save_config(server.settings)

    ttk.Checkbutton(opts, text="每次連線都要我按「允許」", variable=accept_var, command=opt_changed).pack(anchor="w", padx=8)
    ttk.Checkbutton(opts, text="唯讀模式（對方只能看，不能操作）", variable=view_var, command=opt_changed).pack(anchor="w", padx=8)
    ttk.Checkbutton(opts, text="允許遠端執行指令（進階）", variable=shell_var, command=opt_changed).pack(anchor="w", padx=8)
    https_var = tk.BooleanVar(value=bool(server.settings.get("https")))

    def https_changed():
        server.settings["https"] = https_var.get()
        save_config(server.settings)
        messagebox.showinfo("HTTPS", "設定已儲存，重新啟動 LAN Remote 後生效。\n瀏覽器第一次會警告憑證不受信任，選「繼續前往」即可。", parent=root)

    ttk.Checkbutton(opts, text="用 HTTPS（瀏覽器端才能用 H.264 流暢模式，需重啟）", variable=https_var, command=https_changed).pack(anchor="w", padx=8)
    if server.demo:
        ttk.Label(opts, text="⚠ 示範模式：畫面為模擬、不會真的控制這台電腦", foreground="#b00").pack(anchor="w", padx=8, pady=2)
    if server.settings.get("port_fallback"):
        ttk.Label(opts, text=f"⚠ 預設埠被其他程式佔用，這次改用 {server.port}（對方仍可自動找到）", foreground="#b60").pack(anchor="w", padx=8, pady=2)

    # ---- viewers & peers -------------------------------------------------
    mid = ttk.Frame(root)
    mid.pack(fill="both", expand=True, **pad)
    vf = ttk.LabelFrame(mid, text="目前連線中的使用者")
    vf.pack(side="left", fill="both", expand=True, padx=(0, 4))
    viewers_lb = tk.Listbox(vf, height=5)
    viewers_lb.pack(fill="both", expand=True, padx=4, pady=4)
    ttk.Button(vf, text="全部斷線", command=server.kick_all).pack(pady=(0, 4))

    pf = ttk.LabelFrame(mid, text="連線到其他電腦（控制別台）")
    pf.pack(side="left", fill="both", expand=True, padx=(4, 0))
    ttk.Label(pf, text="區網上找到的電腦（點兩下連線）:", foreground="#555").pack(anchor="w", padx=4)
    peers_lb = tk.Listbox(pf, height=4)
    peers_lb.pack(fill="both", expand=True, padx=4, pady=2)
    peer_urls: list[str] = []

    from tkinter import simpledialog

    def connect_native(url: str):
        pin = simpledialog.askstring("連線", f"連線到 {url}\n請輸入對方視窗顯示的 PIN 碼：", parent=root, show="*")
        if pin:
            launch_viewer(url, pin, server.name)

    def selected_peer_url():
        sel = peers_lb.curselection()
        if sel and sel[0] < len(peer_urls):
            return peer_urls[sel[0]]
        return None

    def open_peer(_=None):
        u = selected_peer_url()
        if u:
            connect_native(u)

    def open_peer_browser():
        u = selected_peer_url()
        if u:
            webbrowser.open(u)

    peers_lb.bind("<Double-Button-1>", open_peer)
    pbtns = ttk.Frame(pf)
    pbtns.pack(pady=(0, 4))
    ttk.Button(pbtns, text="連線（原生視窗）", command=open_peer).pack(side="left")
    ttk.Button(pbtns, text="用瀏覽器", command=open_peer_browser, width=8).pack(side="left", padx=4)
    ttk.Label(pf, text="或輸入對方 IP / 網址:", foreground="#555").pack(anchor="w", padx=4)
    manual_row = ttk.Frame(pf)
    manual_row.pack(fill="x", padx=4, pady=(0, 4))
    manual_var = tk.StringVar()
    manual_entry = ttk.Entry(manual_row, textvariable=manual_var)
    manual_entry.pack(side="left", fill="x", expand=True)

    def open_manual(_=None):
        target = manual_var.get().strip()
        if not target:
            return
        if "://" not in target:
            target = "http://" + target
        if target.count(":") < 2:  # no port given
            target = target.rstrip("/") + f":{server.port}"
        connect_native(target.rstrip("/"))

    manual_entry.bind("<Return>", open_manual)
    ttk.Button(manual_row, text="連線", command=open_manual, width=5).pack(side="left", padx=(4, 0))

    # ---- chat / log ------------------------------------------------------
    lf = ttk.LabelFrame(root, text="訊息 / 紀錄")
    lf.pack(fill="both", expand=True, **pad)
    log_text = tk.Text(lf, height=6, state="disabled", wrap="word")
    log_text.pack(fill="both", expand=True, padx=4, pady=4)
    chat_row = ttk.Frame(lf)
    chat_row.pack(fill="x", padx=4, pady=(0, 4))
    chat_var = tk.StringVar()
    chat_entry = ttk.Entry(chat_row, textvariable=chat_var)
    chat_entry.pack(side="left", fill="x", expand=True)

    def send_chat(_=None):
        txt = chat_var.get().strip()
        if txt:
            server.host_chat(txt)
            chat_var.set("")

    chat_entry.bind("<Return>", send_chat)
    ttk.Button(chat_row, text="傳送給連線者", command=send_chat).pack(side="left", padx=4)

    bottom = ttk.Frame(root)
    bottom.pack(fill="x", padx=10, pady=(0, 8))
    ttk.Button(bottom, text="在本機瀏覽器開啟", command=lambda: webbrowser.open(f"{server.scheme}://127.0.0.1:{server.port}/")).pack(side="left")
    status_var = tk.StringVar(value="")
    ttk.Label(bottom, textvariable=status_var).pack(side="right")

    shown_events = 0

    def refresh():
        nonlocal shown_events
        ips = local_ips()
        url_text.config(state="normal")
        url_text.delete("1.0", "end")
        url_text.insert("1.0", "\n".join(f"{server.scheme}://{ip}:{server.port}/" for ip in ips) or "(找不到網路介面)")
        url_text.config(state="disabled")
        viewers_lb.delete(0, "end")
        for v in server.viewer_list():
            mins = int((time.time() - v["since"]) // 60)
            viewers_lb.insert("end", f"{v['name']}  {v['ip']}  已連線 {mins} 分鐘  {v['bytes'] / 1e6:.1f} MB")
        peers_lb.delete(0, "end")
        peer_urls.clear()
        for p in server.discovery.list():
            peers_lb.insert("end", f"{p.get('name')}  {p.get('ip')}  ({p.get('os')})")
            peer_urls.append(f"{'https' if p.get('https') else 'http'}://{p['ip']}:{p.get('port', 7788)}")
        if len(events) > shown_events or shown_events > len(events):
            log_text.config(state="normal")
            if shown_events > len(events):
                log_text.delete("1.0", "end")
                shown_events = 0
            for line in events[shown_events:]:
                log_text.insert("end", line + "\n")
            shown_events = len(events)
            log_text.see("end")
            log_text.config(state="disabled")
        status_var.set(f"埠 {server.port} · {len(server.viewers)} 人連線 · {len(server.discovery.peers)} 台裝置")
        root.after(1500, refresh)

    refresh()

    def on_close():
        if server.viewers and not messagebox.askokcancel("離開", "還有人連線中，確定要關閉 LAN Remote 嗎？", parent=root):
            return
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()
