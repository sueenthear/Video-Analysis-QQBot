from douyin_core.douyin_parser import extract_share_url


SAMPLE_SHARE_TEXT = (
    "9.20 复制打开抖音，看看【偷偷摸摸的小k的作品】一起去潜水吗 "
    "# 潜水 # 仟之k # 乳胶 # ... "
    "https://v.douyin.com/PSvrGQmOOTk/ uFU:/ m@Q.xs 09/01 :2pm"
)


def test_extract_sample_douyin_short_link():
    assert extract_share_url(SAMPLE_SHARE_TEXT) == "https://v.douyin.com/PSvrGQmOOTk/"
