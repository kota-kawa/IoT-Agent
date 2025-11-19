from llama_cpp import Llama


def main() -> None:
    # TinySwallow 1.5B (軽量量子化版) のパス
    model_path = (
        "TinySwallow-1.5B-Instruct-Q5_K_S.gguf"
    )

    llm = Llama(
        model_path=model_path,
        # コンテキスト長。まずは 1024 くらいに抑える
        n_ctx=1024,
        # 一度に処理するトークン数。Jetson では 32～64 くらいが安全
        n_batch=32,
        # GPU に載せるレイヤー数
        # まずは 16 くらいから。まだ OOM なら 12 → 8 と減らしていく
        n_gpu_layers=16,
        # CPU スレッド数（お好みで）
        n_threads=4,
        # ログ出力
        verbose=True,
        # 再現性用（なくてもOK）
        seed=42,
    )

    prompt = "太陽系の惑星を列挙してください。A:"
    output = llm(
        prompt,
        max_tokens=64,
        stop=["\n"],
        temperature=0.7,
        top_p=0.9,
    )
    print(output["choices"][0]["text"])


if __name__ == "__main__":
    main()

