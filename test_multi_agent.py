#!/usr/bin/env python3
"""
マルチエージェント連携機能のテストスクリプト

このスクリプトは、IoT Agent のマルチエージェント連携機能をテストします。
実際のエージェントが起動していない場合でも、API の動作を確認できます。
"""

import json
import sys


def test_agent_selection():
    """エージェント自動選択機能のテスト"""
    print("=" * 60)
    print("エージェント自動選択機能のテスト")
    print("=" * 60)
    
    # app モジュールをインポート（環境変数が必要）
    import os
    os.environ.setdefault('OPENAI_API_KEY', 'dummy-key-for-testing')
    
    from app import _select_optimal_agent_for_task
    
    test_cases = [
        ("温度センサーのエラーの解決方法を教えて", "faq"),
        ("Googleで最新のIoTトレンドを検索して", "browser"),
        ("デバイスの温度を測定して", "iot"),
        ("Webサイトから情報を収集して", "browser"),
        ("使い方を教えて", "faq"),
        ("LEDを点滅させて", "iot"),
    ]
    
    for task, expected in test_cases:
        selected = _select_optimal_agent_for_task(task)
        status = "✓" if selected == expected else "✗"
        print(f"{status} タスク: {task}")
        print(f"  期待: {expected}, 結果: {selected}")
        print()
    
    return True


def test_agent_descriptions():
    """エージェント説明の表示テスト"""
    print("=" * 60)
    print("エージェント説明の表示")
    print("=" * 60)
    
    import os
    os.environ.setdefault('OPENAI_API_KEY', 'dummy-key-for-testing')
    
    from app import (
        FAQ_AGENT_DESCRIPTION,
        BROWSER_AGENT_DESCRIPTION,
        FAQ_AGENT_ENDPOINTS,
        BROWSER_AGENT_ENDPOINTS,
    )
    
    print("\n【FAQ エージェント】")
    print(FAQ_AGENT_DESCRIPTION)
    print("\nエンドポイント:")
    for name, path in FAQ_AGENT_ENDPOINTS.items():
        print(f"  - {name}: {path}")
    
    print("\n" + "-" * 60)
    print("\n【Browser エージェント】")
    print(BROWSER_AGENT_DESCRIPTION)
    print("\nエンドポイント:")
    for name, path in BROWSER_AGENT_ENDPOINTS.items():
        print(f"  - {name}: {path}")
    
    return True


def test_endpoint_availability():
    """API エンドポイントの存在確認"""
    print("\n" + "=" * 60)
    print("API エンドポイントの存在確認")
    print("=" * 60)
    
    import os
    os.environ.setdefault('OPENAI_API_KEY', 'dummy-key-for-testing')
    
    from app import app
    
    expected_endpoints = [
        '/api/agents/request-help',
        '/api/agents/respond',
    ]
    
    available_rules = {rule.rule for rule in app.url_map.iter_rules()}
    
    for endpoint in expected_endpoints:
        if endpoint in available_rules:
            print(f"✓ {endpoint} - 存在")
        else:
            print(f"✗ {endpoint} - 不在")
    
    return True


def show_example_request():
    """使用例の表示"""
    print("\n" + "=" * 60)
    print("使用例")
    print("=" * 60)
    
    print("\n【FAQ エージェントに質問する例】")
    example_faq = {
        "task": "Raspberry Pi のカメラモジュールの設定方法を教えて",
        "agent": "faq"
    }
    print(f"POST /api/agents/request-help")
    print(f"Content-Type: application/json")
    print()
    print(json.dumps(example_faq, ensure_ascii=False, indent=2))
    
    print("\n" + "-" * 60)
    print("\n【Browser エージェントに Web タスクを依頼する例】")
    example_browser = {
        "task": "気象庁のサイトから東京の天気予報を取得して",
        "agent": "browser"
    }
    print(f"POST /api/agents/request-help")
    print(f"Content-Type: application/json")
    print()
    print(json.dumps(example_browser, ensure_ascii=False, indent=2))
    
    print("\n" + "-" * 60)
    print("\n【自動でエージェントを選択する例】")
    example_auto = {
        "task": "温度センサーのエラーの解決方法を調べて",
        "agent": "auto"
    }
    print(f"POST /api/agents/request-help")
    print(f"Content-Type: application/json")
    print()
    print(json.dumps(example_auto, ensure_ascii=False, indent=2))


def main():
    """メイン関数"""
    print("\n")
    print("╔" + "=" * 58 + "╗")
    print("║" + " " * 58 + "║")
    print("║" + "   IoT Agent マルチエージェント連携機能テスト   ".center(58) + "║")
    print("║" + " " * 58 + "║")
    print("╚" + "=" * 58 + "╝")
    print()
    
    try:
        # テストを実行
        test_agent_descriptions()
        test_agent_selection()
        test_endpoint_availability()
        show_example_request()
        
        print("\n" + "=" * 60)
        print("すべてのテストが完了しました ✓")
        print("=" * 60)
        print()
        
        return 0
        
    except Exception as e:
        print(f"\nエラーが発生しました: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
