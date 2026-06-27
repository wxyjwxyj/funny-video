"""向后兼容的 AI 视频入口，实际逻辑已迁移到 run_topic.py。"""
import sys
if "--topic" not in sys.argv:
    sys.argv.insert(1, "--topic")
    sys.argv.insert(2, "ai")

from run_topic import main
if __name__ == "__main__":
    main()
