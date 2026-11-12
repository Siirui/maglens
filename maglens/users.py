"""Manage MagLens accounts (stored in <data_dir>/auth.json).

    python -m maglens.users add siri --role admin      # prompts for a password
    python -m maglens.users add friend                  # reader by default
    python -m maglens.users passwd friend
    python -m maglens.users remove friend
    python -m maglens.users scope trial 387f45a7acf6    # reader may only open these issue ids
    python -m maglens.users scope trial                 # every issue again
    python -m maglens.users list
"""
from __future__ import annotations

import argparse
import getpass
import sys

from .auth import ROLES, Auth
from .config import load_config


def _ask_password() -> str:
    while True:
        pw = getpass.getpass("密码: ")
        if len(pw) < 8:
            print("至少 8 位。")
            continue
        if pw != getpass.getpass("再输一次: "):
            print("两次不一致。")
            continue
        return pw


def main(argv=None):
    ap = argparse.ArgumentParser(description="管理 MagLens 账号")
    ap.add_argument("--data-dir", help="数据目录（默认读 config.toml，部署时指向 dist 目录）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add", help="新增账号（已存在则覆盖密码和角色）")
    a.add_argument("name")
    a.add_argument("--role", choices=ROLES, default="reader")
    p = sub.add_parser("passwd", help="修改密码")
    p.add_argument("name")
    r = sub.add_parser("remove", help="删除账号")
    r.add_argument("name")
    s = sub.add_parser("scope", help="限定读者能看的杂志（不填 id 就是全部）")
    s.add_argument("name")
    s.add_argument("mags", nargs="*", help="杂志 id（list 里能看到）")
    sub.add_parser("list", help="列出账号和杂志 id")
    args = ap.parse_args(argv)

    cfg = load_config(data_dir=args.data_dir)
    auth = Auth(cfg.data_dir / "auth.json")
    if args.cmd == "add":
        old = auth.users.get(args.name, {})
        mags = old.get("mags") if args.role == "reader" else None  # re-adding keeps an issue limit
        auth.set_user(args.name, _ask_password(), args.role, mags)
        print(f"已保存 {args.name}（{args.role}） → {auth.path}" + (f"，仍然只能看：{' '.join(mags)}" if mags else ""))
    elif args.cmd == "passwd":
        if args.name not in auth.users:
            sys.exit(f"没有这个账号：{args.name}")
        u = auth.users[args.name]
        auth.set_user(args.name, _ask_password(), u["role"], u.get("mags"))
        print("密码已更新")
    elif args.cmd == "remove":
        print("已删除" if auth.remove_user(args.name) else f"没有这个账号：{args.name}")
    elif args.cmd == "scope":
        if args.name not in auth.users:
            sys.exit(f"没有这个账号：{args.name}")
        if auth.users[args.name]["role"] == "admin":
            sys.exit("管理员可以看所有杂志，不需要限定")
        if args.mags:
            from .server import available
            from .store import Store
            known = ({m["id"] for m in Store(cfg.db_path, pages_dir=cfg.data_dir / "pages").magazines()
                      if available(m)} if cfg.db_path.exists() else set())
            bad = [x for x in args.mags if x not in known]
            if bad:
                sys.exit(f"没有这些已翻完的杂志 id：{' '.join(bad)}（用 list 查看）")
        auth.set_mags(args.name, args.mags or None)
        print(f"{args.name} 可以看：{' '.join(args.mags) if args.mags else '全部'}")
    else:
        if not auth.users:
            print("还没有账号（未启用登录，本地访问时所有人都是管理员）")
        for u in auth.users.values():
            print(f"{u['name']}\t{u['role']}\t{'全部' if u.get('mags') is None else ' '.join(u['mags']) or '（无）'}")
        if cfg.db_path.exists():
            from .store import Store
            print("\n杂志 id：")
            for m in Store(cfg.db_path, pages_dir=cfg.data_dir / "pages").magazines():
                print(f"  {m['id']}\t{m['title']}")


if __name__ == "__main__":
    main()
