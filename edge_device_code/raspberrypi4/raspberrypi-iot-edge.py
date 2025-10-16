#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
from llama_cpp import Llama




# ===== 設定 =====
MODEL_PATH = "tinyllama-1.1b-chat-v1.0.Q4_K_M.gguf"  # 自分のGGUFに変更
N_THREADS  = 4        # Pi4は4
N_CTX      = 1024     # 512～1024程度
TEMP       = 0.7

SYSTEM_CHAT_MSG = "You are very simple AI assistant."

def read_single_user_prompt() -> str:
    """
    1回だけユーザーから入力を受け取る。
    - 引数があればそれをプロンプトとして使用。
    - なければ標準入力から1行読み取る。
    空なら終了コード2で終了。
    """
    if len(sys.argv) > 1:
        # 引数をスペース連結して1つのプロンプトに
        return " ".join(sys.argv[1:]).strip()

    try:
        # 対話入力
        print("質問を1回だけ入力してEnterを押してください（終了: Ctrl-D / Ctrl-Z）:")
        user_in = sys.stdin.readline()
    except KeyboardInterrupt:
        user_in = ""
    if user_in is None:
        user_in = ""
    user_in = user_in.strip()
    if not user_in:
        print("入力が空のため終了します。", file=sys.stderr)
        sys.exit(2)
    return user_in

def main():
    # モデル存在チェック
    if not os.path.exists(MODEL_PATH):
        print(f"エラー: GGUF が見つかりません: {MODEL_PATH}", file=sys.stderr)
        sys.exit(1)

    # Llama インスタンス作成
    try:
        llm = Llama(
            model_path=MODEL_PATH,
            n_threads=N_THREADS,
            n_ctx=N_CTX,
            verbose=False,
            # chat_format="llama-3",  # 使える場合のみ。ダメならコメントアウトのまま
        )
    except Exception:
        print("モデル読み込みに失敗しました。swap拡張や n_ctx 縮小、量子化軽量化を試してください。", file=sys.stderr)
        raise

    # === ここから「1入出力のみ」 ===
    user_prompt = read_single_user_prompt()

    # まずは Chat Completion を試す（使えない場合は例外でフォールバック）
    try:
        chat_res = llm.create_chat_completion(
            messages=[
                {"role": "system", "content": SYSTEM_CHAT_MSG},
                {"role": "user",   "content": user_prompt},
            ],
            temperature=TEMP,
        )
        print(chat_res["choices"][0]["message"]["content"])
    except Exception:
        # --- Fallback: Completion API（プロンプトを自分で整形） ---
        prompt = (
            "### Instruct:\n"
            "You should answer very simple.\n"
            "### User:\n"          
            f"{user_prompt}\n"
            "### Assistant:\n"
        )
        comp = llm.create_completion(
            prompt=prompt,
            max_tokens=512,
            temperature=TEMP,
            top_p=0.9,
            repeat_penalty=1.1,
        )
        # llama.cpp系は "text" キーに応答が入る
        print(comp["choices"][0]["text"])

    # ここで即終了（追加の質問は行わない）
    sys.exit(0)

if __name__ == "__main__":
    main()
