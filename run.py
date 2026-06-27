"""向后兼容的搞笑视频入口，实际逻辑已迁移到 run_topic.py。

用法：python run.py [--pages N] [--tag-batch N] [--min-score N] [--skip-collect] [--skip-tag] [--skip-douyin] [--skip-xhs]
"""
import sys
# 注入 --topic funny，其余参数原样透传
if "--topic" not in sys.argv:
    sys.argv.insert(1, "--topic")
    sys.argv.insert(2, "funny")

from run_topic import main
if __name__ == "__main__":
    main()
