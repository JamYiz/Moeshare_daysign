import os
import re
import time
import httpx, h2
import traceback
import random
from contextlib import contextmanager
from bs4 import BeautifulSoup
import json

# --- 漫画不当BBS 配置 ---
MANHUABUDANG_HOST = "www.manhuabudangbbs.com"
DEFAULT_USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36 Edg/138.0.0.0'


# --- Telegram 通知函数 ---
def push_notification(title: str, content: str) -> None:
    """
    发送 Telegram 通知。
    需要设置 TG_USER_ID 和 TG_BOT_TOKEN 环境变量。
    """

    def telegram_send_message(text: str, chat_id: str, token: str, silent: bool = False) -> None:
        try:
            r = httpx.post(url=f'https://api.telegram.org/bot{token}/sendMessage',
                           json={
                               'chat_id': chat_id,
                               'text': text,
                               'disable_notification': silent,
                               'disable_web_page_preview': True,
                           }, timeout=10)
            r.raise_for_status()
            print(f"✅ Telegram 通知已发送：{title}")
        except httpx.RequestError as exc:
            print(f"❌ 发送 Telegram 通知时发生请求错误: {exc}")
        except httpx.HTTPStatusError as exc:
            print(f"❌ 发送 Telegram 通知失败，HTTP 状态码: {exc.response.status_code}, 响应: {exc.response.text}")
        except Exception as e:
            print(f"❌ 发送 Telegram 通知过程中发生未知错误: {e}")

    chat_id = os.getenv('TG_USER_ID')
    bot_token = os.getenv('TG_BOT_TOKEN')

    if chat_id and bot_token:
        telegram_send_message(f'{title}\n\n{content}', chat_id, bot_token)
    else:
        print("⚠️ 未设置 TG_USER_ID 或 TG_BOT_TOKEN，跳过 Telegram 通知。")


def _get_punch_button_info(client: httpx.Client, _request_context_manager) -> tuple[bool, bool, str | None]:
    """
    访问个人中心页面，查找每日打卡按钮，并尝试提取打卡所需的动态参数。
    返回 (是否找到按钮, 按钮是否禁用/已打卡, verifyhash)
    """
    user_page_url = f'https://{MANHUABUDANG_HOST}/u.php'  # 重新确认访问 u.php
    print(f"尝试访问个人中心页面: {user_page_url} 查找每日打卡按钮和参数...")

    is_button_found = False
    is_button_disabled = True  # 默认假设禁用
    verify_hash = None
    full_page_content = ""  # 用于存储整个页面内容

    try:
        with _request_context_manager('GET', user_page_url) as response:
            if response.status_code == 200:
                full_page_content = response.text  # 获取完整页面内容

                soup = BeautifulSoup(full_page_content, 'html.parser')

                # 修改识别逻辑：
                # 寻找 class 包含 'card' 并且 'card_old' 或 'card_new' 的 div
                # 同时检查其内部的 span 文本是否包含 '打卡'
                punch_div = None

                # 尝试找到未打卡按钮: class="card fr" 且文本为 "每日打卡"
                new_punch_div = soup.find('div', class_='card', string=re.compile(r'每日打卡'))
                if new_punch_div and 'card_old' not in new_punch_div.get('class', []):  # 确保不是已打卡状态的旧按钮
                    punch_div = new_punch_div

                # 如果没找到未打卡按钮，尝试找到已打卡按钮: class="card fr card_old" 且文本包含 "天打卡"
                if not punch_div:
                    old_punch_div = soup.find('div', class_='card_old', string=re.compile(r'天打卡'))
                    if old_punch_div:
                        punch_div = old_punch_div

                if punch_div:
                    is_button_found = True  # 找到打卡相关的 div

                    span_text = punch_div.get_text(strip=True)  # 获取 div 内的文本，包括 span 的
                    button_classes = punch_div.get('class', [])

                    if '每日打卡' in span_text and 'card_old' not in button_classes:  # 确保是可点击的“每日打卡”
                        is_button_disabled = False
                        print("✅ 找到 '每日打卡' 按钮。状态：未打卡，可点击。")
                    elif 'card_old' in button_classes and '连续' in span_text and '天打卡' in span_text:
                        is_button_disabled = True
                        print(f"✅ 找到 '连续打卡' 按钮：{span_text}。状态：今日已打卡。")
                    else:
                        is_button_found = False  # 找到了 div 但不是预期的打卡按钮状态
                        print(f"❌ 找到类似打卡按钮的元素，但状态未知。内容：'{span_text}'，类：{button_classes}")
                else:
                    is_button_found = False
                    print("❌ 未找到明确的 '每日打卡' 或 '连续打卡' 按钮结构。")

                # 提取 verifyhash (无论哪种打卡按钮状态，只要是登录页面，verifyhash应该都在)
                if is_button_found or not any(keyword in full_page_content for keyword in
                                              ["登录", "注册", "未登录", "请登录", "Login", "Register"]):
                    script_tags = soup.find_all('script')
                    for script in script_tags:
                        if script.string:
                            verify_hash_match = re.search(r"var\s+verifyhash\s*=\s*'([a-f0-9]+)'", script.string)
                            if verify_hash_match:
                                verify_hash = verify_hash_match.group(1)
                                print(f"✅ 成功从页面中提取打卡参数：verifyhash={verify_hash}")
                                break

                if not is_button_found:  # 如果没有找到按钮
                    print("❌ 登录状态验证失败：未能识别打卡按钮。")
                    # 再次检查常见的未登录提示词
                    if any(keyword in full_page_content for keyword in
                           ["登录", "注册", "未登录", "请登录", "Login", "Register"]):
                        print("ℹ️ 页面显示登录/注册/未登录相关内容。这通常意味着 Cookie 无效或已过期。")
                    print(
                        f"\n--- 获取到的完整页面内容 (请仔细检查是否为登录后的个人中心页，可能按钮结构有变) ---\n{full_page_content}\n--- 完整页面内容结束 ---")
                    return False, False, None

            else:
                print(f"❌ 访问个人中心页面失败，HTTP 状态码: {response.status_code}")
                return False, False, None

    except httpx.RequestError as exc:
        print(f"❌ 访问个人中心页面时发生请求错误: {exc}")
        traceback.print_exc()
        return False, False, None
    except Exception as e:
        print(f"❌ 查找打卡按钮或参数过程中发生未知错误: {e}")
        traceback.print_exc()
        # 即使在解析过程中出错，也打印出获取到的页面内容，以便诊断
        if full_page_content:
            print(
                f"\n--- 获取到的完整页面内容 (请仔细检查是否为登录后的个人中心页，可能按钮结构有变) ---\n{full_page_content}\n--- 完整页面内容结束 ---")
        return False, False, None

    return is_button_found, is_button_disabled, verify_hash


def _perform_daily_punch(client: httpx.Client, _request_context_manager, verify_hash: str | None) -> bool:
    """
    模拟点击“每日打卡”按钮进行打卡。
    """
    punch_url = f'https://{MANHUABUDANG_HOST}/jobcenter.php?action=punch'
    punch_data = {
        'step': '2',
        'jobid': '14',  # 从 onclick="punchJob(14)" 提取的 jobid
    }
    if verify_hash:
        punch_data['verify'] = verify_hash

    print(f"正在提交每日打卡请求到: {punch_url}，数据: {punch_data}")
    try:
        with _request_context_manager('POST', punch_url, data=punch_data, headers={
            'Referer': f'https://{MANHUABUDANG_HOST}/u.php',  # Referer 再次改回 u.php
            'Content-Type': 'application/x-www-form-urlencoded',
        }) as response:
            print(f"打卡请求状态码: {response.status_code}")
            if response.status_code == 200:
                ajax_response_match = re.search(r'<ajax><!\[CDATA\[(.*?)\]\]></ajax>', response.text, re.DOTALL)

                if ajax_response_match:
                    json_str_in_cdata = ajax_response_match.group(1).strip()
                    if '"flag":"1"' in json_str_in_cdata or '威望' in json_str_in_cdata or '成功' in json_str_in_cdata:
                        print(f"✅ 每日打卡成功！漫画不当BBS消息: {json_str_in_cdata}")
                        return True
                    else:
                        print(f"❌ 每日打卡失败。漫画不当BBS消息: {json_str_in_cdata}")
                        return False
                else:
                    if '成功' in response.text or '您今天已打卡' in response.text or '威望' in response.text:
                        print(f"✅ 每日打卡成功！(从页面内容判断)")
                        return True
                    else:
                        print(f"❌ 每日打卡失败：未从响应中解析到预期的成功消息。响应内容：\n{response.text[:500]}...")
                        return False
            else:
                print(f"❌ 每日打卡请求失败，HTTP 状态码: {response.status_code}")
                return False
    except httpx.RequestError as exc:
        print(f"❌ 每日打卡请求过程中发生错误: {exc}")
        traceback.print_exc()
        return False
    except Exception as e:
        print(f"❌ _perform_daily_punch 提交过程中发生未知错误: {e}")
        traceback.print_exc()
        return False


def daysign(cookies: dict) -> bool:
    if not cookies:
        print("❌ 传入 daysign 的 cookies 字典为空，无法执行签到。")
        return False

    with httpx.Client(cookies=cookies, http2=True) as client:

        @contextmanager
        def _request(method, url, *args, **kwargs):
            extra_headers = kwargs.pop('headers', {})

            final_headers = {
                'user-agent': DEFAULT_USER_AGENT,
                'x-requested-with': 'XMLHttpRequest',
                'dnt': '1',
                'accept': '*/*',
                'sec-ch-ua-mobile': '?0',
                'sec-ch-ua-platform': 'macOS',
                'sec-fetch-site': 'same-origin',
                'sec-fetch-mode': 'cors',
                'sec-fetch-dest': 'empty',
                'referer': f'https://{MANHUABUDANG_HOST}/',  # 默认referer
                'accept-language': 'en-US,en;q=0.9,zh-CN;q=0.8,zh-TW;q=0.7,zh-HK;q=0.5,en-US;q=0.3,en;q=0.2',
            }
            final_headers.update(extra_headers)

            response = client.request(method=method, url=url,
                                      headers=final_headers,
                                      timeout=30,
                                      *args, **kwargs)
            try:
                response.raise_for_status()
                yield response
            finally:
                response.close()

        is_button_found, is_button_disabled, verify_hash = _get_punch_button_info(client, _request)

        if not is_button_found:
            error_msg = "❗ 未能识别打卡按钮。这可能是因为网站结构再次变化，或 Cookie 无效。"
            print(error_msg)
            push_notification("漫画不当BBS签到通知", f"签到失败！\n{error_msg}")
            print(f"\n--- 脚本执行结束 ---")
            return False

        if is_button_disabled:
            print("ℹ️ 今日已打卡或按钮处于禁用状态。跳过打卡操作。")
            status_msg = "✅ 漫画不当BBS签到：今日已打卡。"
            print(status_msg)
            push_notification("漫画不当BBS签到通知", status_msg)
            print(f"\n--- 脚本执行结束 ---")
            return True

        print("准备执行每日打卡。")
        punch_success = _perform_daily_punch(client, _request, verify_hash)
        if punch_success:
            push_notification("漫画不当BBS签到通知", f"每日打卡成功！")
        else:
            push_notification("漫画不当BBS签到通知", f"每日打卡失败。")

        print(f"\n--- 脚本执行结束 ---")
        return punch_success


def retrieve_cookies_from_fetch(env: str) -> dict:
    fetch_command_string = os.getenv(env)
    if not fetch_command_string:
        print(f"❌ 错误: 环境变量 '{env}' 未设置。")
        raise ValueError(f"Environment variable '{env}' is not set.")

    cookie_str = None
    cookie_match_single = re.search(r"(?:'Cookie'|\"Cookie\"|'cookie'|\"cookie\"):\s*'(.*?)'", fetch_command_string)
    cookie_match_double = re.search(r"(?:'Cookie'|\"Cookie\"|'cookie'|\"cookie\"):\s*\"(.*?)\"", fetch_command_string)

    if cookie_match_single:
        cookie_str = cookie_match_single.group(1)
    elif cookie_match_double:
        cookie_str = cookie_match_double.group(1)

    if cookie_str:
        print(f"✅ 成功从fetch命令中提取Cookie字符串。")
        cookie_str = cookie_str.replace('\\"', '"')
        return dict(s.strip().split('=', maxsplit=1) for s in cookie_str.split(';'))
    else:
        print(f"❌ 警告: 未能从fetch命令中直接解析到 'Cookie' 键。")
        print(f"请检查环境变量 '{env}' 的内容，确保 'Cookie' 字段存在且格式正确。")
        print(f"环境变量值（前200字符）：{fetch_command_string[:200]}...")
        raise ValueError(f"无法从fetch命令中提取Cookie信息。请确保 '{env}' 环境变量包含有效的fetch命令且包含Cookie。")


def main():
    print("--- 脚本启动 ---")
    env_name = 'MANHUABUDANG_DAYSIGN'

    cookies = {}
    script_successful = False

    try:
        if os.getenv(env_name):
            try:
                cookies = retrieve_cookies_from_fetch(env_name)
                print("--- 成功从环境变量解析出 Cookie ---")
                print("正在尝试执行签到流程...")
                if cookies:
                    print(f"解析到的Cookie数量: {len(cookies)}")
                    script_successful = daysign(cookies=cookies)
                else:
                    print("❌ Cookie解析结果为空，无法继续执行。")
                    push_notification("漫画不当BBS签到通知", "Cookie解析失败，环境变量可能不正确。")

            except Exception as e:
                error_msg = f"ERROR: 在 main 函数中处理 {env_name} 环境变量或执行 daysign 时发生错误: {e}"
                print(error_msg)
                traceback.print_exc()
                push_notification("漫画不当BBS签到通知", f"脚本运行异常：\n{error_msg}")
        else:
            info_msg = f"INFO: 环境变量 '{env_name}' 未设置。请配置你的 fetch 命令。"
            print(info_msg)
            push_notification("漫画不当BBS签到通知", f"脚本运行失败：\n{info_msg}")

    except Exception as e:
        error_msg = f"ERROR: 脚本主函数发生未捕获的错误: {e}"
        print(error_msg)
        traceback.print_exc()
        push_notification("漫画不当BBS签到通知", f"脚本运行异常：\n{error_msg}")

    print("--- 脚本执行结束 ---")


if __name__ == '__main__':
    main()