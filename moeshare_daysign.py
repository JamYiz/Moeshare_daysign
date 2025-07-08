import os
import re
import time
import httpx, h2
import traceback
import random
from contextlib import contextmanager
from bs4 import BeautifulSoup

# --- 萌享社配置 ---
Moeshare_HOST = "www.moeshare.cc"
DEFAULT_USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36 Edg/138.0.0.0'
FID = 36  # 后花园

# 发帖次数（现在主要由活跃度目标控制，此变量作为最大尝试次数）
MAX_REPLY_ATTEMPTS = int(os.getenv("REPLY_TIMES_MOESHARE", 15))  # 提高最大尝试次数，以防活跃度较低
TARGET_ACTIVITY = 10  # 目标活跃度

# 原始的回复内容列表
AUTO_REPLIES_ORIGINAL = (
    "每日打卡，补充一些活跃度",
    "每天签到打卡，增加点活跃度。",
    "水来水去终成空  日日水区日日水",
    "每天坚持打卡，争取早日升级",
    "每日打卡+签到，增加论坛活跃度。日常签到打卡 又是新的一天",
    "每日打卡，勤劳致富,快快升级~~~~~",
    "每天逛水區，爲了早日升級，努力！每天逛水區，爲了早日升級，努力！",
    "每天水一个贴来增加活跃度啦",
    "为了升级每天都来水一篇 努力加油 ! ! ! ! ! 努力加油 ! ! ! ! !",
    "今天也要坚持打卡，必须努力",
    "今日上班先摸鱼打一个卡，贵在坚持",
    "每日打卡签到，大家加油加油~~~",
)


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
                           }, timeout=10)  # 增加超时时间，避免通知发送卡住
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


# --- 萌享社核心功能函数 ---
def _get_tids_from_forum(client: httpx.Client, _request_context_manager, fid: int, page: int = 1) -> list:
    """
    访问萌享社指定版块的页面，提取普通主题的帖子 ID 列表。
    """
    forum_url = f'https://{Moeshare_HOST}/thread-htm-fid-{fid}-page-{page}.html'
    print(f"尝试访问版块页面: {forum_url} 获取帖子列表...")

    tids = []
    try:
        with _request_context_manager('GET', forum_url) as response:
            print(f"版块页面状态码: {response.status_code}")
            if response.status_code == 200:
                soup = BeautifulSoup(response.text, 'html.parser')
                thread_list_body = soup.find('tbody', id='threadlist')

                if thread_list_body:
                    normal_threads_marker = thread_list_body.find('tr', class_='tr4', string=lambda
                        text: '普通主题' in text if text else False)

                    if normal_threads_marker:
                        current_thread_row = normal_threads_marker.find_next_sibling('tr')

                        while current_thread_row:
                            if 'tr3' in current_thread_row.get('class', []):
                                td_id_tag = current_thread_row.find('td', id=re.compile(r'^td_\d+'))

                                if td_id_tag:
                                    tid_match = re.search(r'td_(\d+)', td_id_tag['id'])
                                    if tid_match:
                                        tid = int(tid_match.group(1))
                                        tids.append(tid)

                            next_sibling = current_thread_row.find_next_sibling('tr')
                            current_thread_row = next_sibling

                    else:
                        print("⚠️ 未找到 '普通主题' 标记行。请检查版块页面结构。")
                else:
                    print("⚠️ 未找到 id='threadlist' 的 tbody。请检查版块页面结构。")

                if tids:
                    print(f"成功从版块 {fid} 页面 {page} 获取到 {len(tids)} 个普通主题帖子ID。")
                else:
                    print(f"❌ 未能从版块 {fid} 页面 {page} 获取到任何帖子ID。请检查 HTML 结构或版块内容。")
            else:
                print(f"❌ 访问版块页面失败，状态码: {response.status_code}")
    except httpx.RequestError as exc:
        print(f"❌ 访问版块页面时发生请求错误: {exc}")
        traceback.print_exc()
    except Exception as e:
        print(f"❌ 获取帖子ID过程中发生未知错误: {e}")
        traceback.print_exc()

    return tids


def _get_current_mb_and_activity(client: httpx.Client, _request_context_manager) -> tuple[int, int]:
    """
    访问用户中心页面，获取当前 MB 数量和活跃度。
    返回 (mb_value, activity_value)
    """
    user_page_url = f'https://{Moeshare_HOST}/u.php'
    print(f"正在获取当前 MB 和活跃度，访问页面: {user_page_url}")
    mb_value = 0
    activity_value = 0
    try:
        with _request_context_manager('GET', user_page_url) as response:
            if response.status_code == 200:
                soup = BeautifulSoup(response.text, 'html.parser')

                p_tags = soup.find_all('p', class_='mb5')

                for p_tag in p_tags:
                    p_text = p_tag.get_text(strip=True)

                    if 'MB：' in p_text:
                        mb_a_tag = p_tag.find('a')
                        if mb_a_tag and mb_a_tag.text.strip().isdigit():
                            mb_value = int(mb_a_tag.text.strip())
                            print(f"✅ 当前 MB: {mb_value}")
                        else:
                            print("⚠️ 未能从 MB 标签的<a>中解析数值。")
                    elif '活跃度：' in p_text:
                        activity_a_tag = p_tag.find('a')
                        if activity_a_tag and activity_a_tag.text.strip().isdigit():
                            activity_value = int(activity_a_tag.text.strip())
                            print(f"✅ 当前活跃度: {activity_value}")
                        else:
                            print("⚠️ 未能从活跃度标签的<a>中解析数值。")

                if mb_value == 0 and activity_value == 0:
                    print("⚠️ 未找到 MB 或活跃度信息标签。")

            else:
                print(f"❌ 获取 MB/活跃度失败，状态码: {response.status_code}")
    except httpx.RequestError as exc:
        print(f"❌ 获取 MB/活跃度时发生请求错误: {exc}")
        traceback.print_exc()
    except Exception as e:
        print(f"❌ 获取 MB/活跃度过程中发生未知错误: {e}")
        traceback.print_exc()
    return mb_value, activity_value


def _check_punch_button_status(client: httpx.Client, _request_context_manager) -> bool:
    """
    访问个人中心页面，检查每日打卡按钮是否可用。
    返回 True 如果按钮可用（没有disabled属性），False 如果按钮禁用。
    """
    user_page_url = f'https://{Moeshare_HOST}/u.php'
    print(f"检查每日打卡按钮状态，访问页面: {user_page_url}")
    try:
        with _request_context_manager('GET', user_page_url) as response:
            if response.status_code == 200:
                soup = BeautifulSoup(response.text, 'html.parser')
                punch_button = soup.find('button', type='button', string='每日打卡')

                if punch_button:
                    if 'disabled' in punch_button.attrs:
                        print("❌ '每日打卡' 按钮已禁用，今天可能已打卡或无法打卡。")
                        return False
                    else:
                        print("✅ '每日打卡' 按钮可用。")
                        return True
                else:
                    print("⚠️ 未找到 '每日打卡' 按钮。请检查页面结构。")
                    info_box = soup.find('div', class_='infoBox')
                    if info_box:
                        print(f"调试信息：在 infoBox 中未找到按钮。infoBox 内容前200字：\n{info_box.prettify()[:500]}...")
                    return False
            else:
                print(f"❌ 访问个人中心页面检查打卡按钮失败，状态码: {response.status_code}")
                return False
    except httpx.RequestError as exc:
        print(f"❌ 检查打卡按钮状态时发生请求错误: {exc}")
        traceback.print_exc()
        return False
    except Exception as e:
        print(f"❌ 检查打卡按钮状态过程中发生未知错误: {e}")
        traceback.print_exc()
        return False


def _perform_daily_punch(client: httpx.Client, _request_context_manager) -> bool:
    """
    模拟点击“每日打卡”按钮进行打卡。
    """
    user_page_url = f'https://{Moeshare_HOST}/u.php'
    print(f"尝试执行每日打卡：首先访问个人中心页面获取动态参数...")
    verify_param = None

    try:
        with _request_context_manager('GET', user_page_url) as response:
            if response.status_code == 200:
                soup = BeautifulSoup(response.text, 'html.parser')
                script_tags = soup.find_all('script')
                for script in script_tags:
                    if script.string:
                        verify_hash_match = re.search(r"var\s+verifyhash\s*=\s*'([a-f0-9]+)'", script.string)
                        if verify_hash_match:
                            verify_param = verify_hash_match.group(1)
                            print(f"✅ 成功从页面中提取打卡参数：verify={verify_param}")
                            break

                if not verify_param:
                    print("⚠️ 未能在个人中心页面找到打卡所需的动态参数 (verify)。将尝试不带这些参数的打卡请求。")
            else:
                print(
                    f"❌ 访问个人中心页面获取打卡参数失败，状态码: {response.status_code}. 将尝试不带这些参数的打卡请求。")
    except httpx.RequestError as exc:
        print(f"❌ 获取打卡参数时发生请求错误: {exc}. 将尝试不带这些参数的打卡请求。")
        traceback.print_exc()
    except Exception as e:
        print(f"❌ _perform_daily_punch 获取参数过程中发生未知错误: {e}. 将尝试不带这些参数的打卡请求。")
        traceback.print_exc()

    punch_url = f'https://{Moeshare_HOST}/jobcenter.php?action=punch'
    if verify_param:
        punch_url += f'&verify={verify_param}'
        punch_data = {'step': '2', 'verify': verify_param}
    else:
        punch_data = {'step': '2'}

    print(f"正在提交每日打卡请求到: {punch_url}，数据: {punch_data}")
    try:
        with _request_context_manager('POST', punch_url, data=punch_data, headers={
            'Referer': user_page_url,
            'Content-Type': 'application/x-www-form-urlencoded',
        }) as response:
            print(f"打卡请求状态码: {response.status_code}")
            if response.status_code == 200:
                ajax_response_match = re.search(r'<ajax><!\[CDATA\[(.*?)\]\]></ajax>', response.text, re.DOTALL)
                if ajax_response_match:
                    message = ajax_response_match.group(1).strip()
                    if '成功' in message or 'success' in message or '您今天已打卡' in message:
                        print(f"✅ 每日打卡成功！萌享社消息: {message}")
                        return True
                    else:
                        print(f"❌ 每日打卡失败。萌享社消息: {message}")
                        return False
                else:
                    print(f"❌ 每日打卡失败：未从响应中解析到预期的AJAX消息。响应内容：\n{response.text[:200]}...")
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


# 实际发送回帖请求的函数
def _auto_reply(client: httpx.Client, _request_context_manager, fid: int, tid: int, content: str) -> bool:
    """
    访问帖子详情页获取必要的表单参数，然后提交回帖。
    """
    print(f"--- 尝试回帖到 TID {tid}，内容: '{content}' ---")

    # 1. 访问帖子详情页，获取回帖所需的动态参数
    post_url = f'https://{Moeshare_HOST}/read-htm-tid-{tid}.html'
    try:
        with _request_context_manager('GET', post_url) as response:
            if response.status_code != 200:
                print(f"❌ 访问帖子详情页失败，状态码: {response.status_code}")
                return False

            soup = BeautifulSoup(response.text, 'html.parser')

            # 查找回帖表单
            form_data = {}
            reply_form = soup.find('form', {'action': re.compile(r'post.php')})

            if not reply_form:
                print("❌ 未找到回帖表单。")
                return False

            # 提取所有隐藏的 input 字段
            for hidden_input in reply_form.find_all('input', type='hidden'):
                name = hidden_input.get('name')
                value = hidden_input.get('value')
                if name:
                    form_data[name] = value

            # 填充回帖内容
            form_data['atc_content'] = content
            form_data['step'] = '2'
            form_data['action'] = 'reply'
            form_data['fid'] = str(fid)
            form_data['tid'] = str(tid)

            # 2. 构造回帖的 POST URL
            submit_url = f'https://{Moeshare_HOST}/post.php'

            # 3. 发送 POST 请求提交回帖
            print(f"正在提交回帖到: {submit_url}")
            with _request_context_manager(
                    'POST',
                    submit_url,
                    data=form_data,
                    headers={
                        'referer': post_url,
                        'content-type': 'application/x-www-form-urlencoded',
                    }
            ) as post_response:
                print(f"回帖提交状态码: {post_response.status_code}")

                if post_response.status_code == 200:
                    ajax_response_match = re.search(r'<ajax><!\[CDATA\[(.*?)\]\]></ajax>', post_response.text,
                                                    re.DOTALL)
                    if ajax_response_match:
                        ajax_message = ajax_response_match.group(1).strip()
                        if '发表成功' in ajax_message or '成功' in ajax_message or 'success' in ajax_message:
                            print(f"✅ 成功回帖到帖子 {tid}！萌享社消息: {ajax_message}")
                            return True
                        elif 'tid-' in str(post_response.url):
                            print(f"✅ 成功回帖到帖子 {tid}！页面重定向到帖子详情页。")
                            return True
                        else:
                            print(f"❌ 回帖失败，萌享社返回消息: {ajax_message}")
                            return False
                    else:
                        if '发表成功' in post_response.text or '成功' in post_response.text or 'tid-' in str(
                                post_response.url):
                            print(f"✅ 成功回帖到帖子 {tid}！")
                            return True
                        else:
                            print(f"❌ 回帖失败，响应内容：\n{post_response.text[:500]}...")
                            error_msg = re.search(r'<div class="f_alert_d" id="J_q_message_tip">(.+?)</div>',
                                                  post_response.text)
                            if error_msg:
                                print(f"错误提示: {error_msg.group(1).strip()}")
                            return False
                else:
                    print(f"❌ 回帖提交请求失败，HTTP 状态码: {post_response.status_code}")
                    return False

    except httpx.RequestError as exc:
        print(f"❌ 回帖请求过程中发生错误: {exc}")
        traceback.print_exc()
        return False
    except Exception as e:
        print(f"❌ _auto_reply 函数执行中发生未知错误: {e}")
        traceback.print_exc()
        return False


def daysign(
        cookies: dict,
        flaresolverr_url: str = None,
        flaresolverr_proxy: str = None,
        # 注意：FlareSolverrHTTPClient 并没有 proxy 参数，如果使用 FlareSolverr，proxy 是在 FlareSolverr 服务端配置的。这个参数在这里是冗余的。
) -> bool:
    # 优先尝试使用 FlareSolverrHTTPClient，如果 flaresolverr_url 未提供，则回退到 httpx.Client
    from flaresolverr import FlareSolverrHTTPClient  # 确保这里能够导入 FlareSolverrHTTPClient
    with (FlareSolverrHTTPClient(
            url=flaresolverr_url,
            cookies=cookies,
            http2=True)
    if flaresolverr_url else httpx.Client(cookies=cookies, http2=True)) as client:

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
                'referer': f'https://{Moeshare_HOST}/',
                'accept-language': 'en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7',
            }
            final_headers.update(extra_headers)

            response = client.request(method=method, url=url,
                                      headers=final_headers,
                                      *args, **kwargs)
            try:
                response.raise_for_status()
                yield response
            finally:
                response.close()

        # 验证登录状态
        login_check_url = f'https://{Moeshare_HOST}/u.php'
        is_logged_in = False
        print(f"尝试访问个人中心页面: {login_check_url} 进行登录状态验证...")
        try:
            with _request('GET', login_check_url) as response:
                print(f"访问个人中心页面状态码: {response.status_code}")
                if response.status_code == 200:
                    soup = BeautifulSoup(response.text, 'html.parser')
                    username_span = soup.find('span', class_='f16 lh_24 b s5 mr5')
                    login_form = soup.find('form', {'action': 'member.php?mod=logging&action=login'})
                    logout_link = soup.find('a', href=re.compile(r'login\.php\?action=quit'))

                    if username_span and username_span.text.strip() and logout_link:
                        print(f"✅ 检测到用户名 '{username_span.text.strip()}' 和 '退出' 链接。初步判断已登录。")
                        is_logged_in = True
                    elif login_form:
                        print("❌ 检测到登录表单。判断为未登录。")
                        is_logged_in = False
                    else:
                        print("⚠️ 未能明确判断登录状态。请检查 HTML 或调整选择器。")
                        print("--- 响应 HTML 的前 500 个字符用于调试 ---")
                        print(response.text[:500])
                        print("----------------------------------------------------------")
                        is_logged_in = False
                else:
                    print(f"❌ HTTP 状态码 {response.status_code} 不是 200。无法检查登录状态。")
                    is_logged_in = False
        except httpx.RequestError as exc:
            print(f"❌ 访问 {exc.request.url} 时发生请求错误: {exc}")
            is_logged_in = False
        except Exception as e:
            print(f"❌ 登录状态验证过程中发生未知错误: {e}")
            traceback.print_exc()
            is_logged_in = False

        # 在这里执行登录失败的通知
        if not is_logged_in:
            error_msg = "❗ 登录状态验证失败：用户未登录或状态不明确。请确保你的 Cookie 有效，并从登录会话中准确提取。"
            print(error_msg)
            push_notification("萌享社签到通知", f"登录失败！请检查 Cookie。\n{error_msg}")
            mb_final, activity_final = _get_current_mb_and_activity(client, _request)
            print(f"\n--- 脚本执行结束 ---")
            print(f"最终 MB: {mb_final}，最终活跃度: {activity_final}")
            return False

        # 获取可用帖子列表，即使后续不发帖，也先尝试获取
        available_tids = _get_tids_from_forum(client, _request, FID)
        if not available_tids:
            warning_msg = "❌ 未能获取到可回复的帖子列表。可能无法进行任何回复。"
            print(warning_msg)
            push_notification("萌享社签到通知", f"未能获取到帖子列表。\n{warning_msg}")

        # --- 新增：强制至少一次回复 ---
        reply_attempts = 0
        current_mb, current_activity = _get_current_mb_and_activity(client, _request)
        if available_tids:
            print("\n--- 强制执行至少一次随机回复 ---")
            target_tid = random.choice(available_tids)
            available_tids.remove(target_tid)
            reply_content = random.choice(list(AUTO_REPLIES_ORIGINAL))
            if _auto_reply(client, _request, FID, target_tid, reply_content):
                reply_attempts += 1
                print(f"✅ 成功执行了首次强制回复。")
                time.sleep(5)
            else:
                print(f"❌ 首次强制回复失败。")
                push_notification("萌享社签到通知", f"首次强制回复失败。")  # 首次回复失败也通知
            current_mb, current_activity = _get_current_mb_and_activity(client, _request)
            print(f"首次回复后，当前 MB: {current_mb}，当前活跃度: {current_activity}")
        else:
            print("⚠️ 没有可用的帖子，无法执行首次强制回复。")
            current_mb, current_activity = _get_current_mb_and_activity(client, _request)
        # --- 强制回复结束 ---

        # --- 检查打卡按钮状态 (在强制回复之后) ---
        is_punch_button_enabled = _check_punch_button_status(client, _request)
        if not is_punch_button_enabled:
            status_msg = "❗ '每日打卡' 按钮已禁用。根据你的要求，停止进一步发帖。"
            print(status_msg)
            # 这里的通知可以更具体，例如报告当前的活跃度
            push_notification("萌享社签到通知",
                              f"每日打卡按钮已禁用。\n当前 MB: {current_mb}，活跃度: {current_activity}")
            mb_final, activity_final = _get_current_mb_and_activity(client, _request)
            print(f"\n--- 脚本执行结束 ---")
            print(f"最终 MB: {mb_final}，最终活跃度: {activity_final}")
            return True  # 视为成功完成流程，只是因为打卡按钮禁用而提前结束
        # --- 检查结束 ---

        # 活跃度检查和循环发帖逻辑
        while current_activity < TARGET_ACTIVITY and reply_attempts < MAX_REPLY_ATTEMPTS:
            print(f"\n--- 活跃度 {current_activity}/{TARGET_ACTIVITY} - 正在进行第 {reply_attempts + 1} 次回帖尝试 ---")

            if not available_tids:
                print("⚠️ 没有更多帖子可回复了，且活跃度未达标。请尝试刷新帖子列表或检查版块。")
                push_notification("萌享社签到通知", f"回帖失败：没有更多帖子可回复。当前活跃度: {current_activity}")
                break

            available_replies = list(AUTO_REPLIES_ORIGINAL)

            target_tid = random.choice(available_tids)
            try:
                available_tids.remove(target_tid)
            except ValueError:
                pass

            reply_content = random.choice(available_replies)
            try:
                available_replies.remove(reply_content)
            except ValueError:
                pass

            print(f"选择帖子 ID: {target_tid} 进行回复。")
            print(f"回复内容: '{reply_content}'")

            try:
                auto_reply_success = _auto_reply(client, _request, FID, target_tid, reply_content)
                reply_attempts += 1

                if auto_reply_success:
                    print(f"✅ 回复成功！等待更新活跃度...")
                    time.sleep(5)
                    current_mb, current_activity = _get_current_mb_and_activity(client, _request)
                    if current_activity >= TARGET_ACTIVITY:
                        print(f"🎉 活跃度已达到目标 {TARGET_ACTIVITY}！")
                        push_notification("萌享社签到通知",
                                          f"活跃度已达标！当前 MB: {current_mb}，活跃度: {current_activity}")
                        break
                else:
                    print(f"❌ 本次回帖失败。")
                    push_notification("萌享社签到通知", f"第 {reply_attempts} 次回帖失败。")

            except Exception as e:
                print(f"❌ 回帖过程中发生错误: {e}")
                traceback.print_exc()
                push_notification("萌享社签到通知", f"回帖过程中发生错误: {e}")

            if current_activity < TARGET_ACTIVITY and reply_attempts < MAX_REPLY_ATTEMPTS:
                sleep_time = random.randint(40, 60)
                print(f"等待 {sleep_time} 秒后进行下一次回帖尝试...")
                time.sleep(sleep_time)

        print("--- 回帖循环结束 ---")

        # 如果活跃度未达标，但打卡按钮是可用的，则尝试打卡一次
        if current_activity < TARGET_ACTIVITY and is_punch_button_enabled:
            print("活跃度未完全达标，但打卡按钮可用，尝试执行每日打卡。")
            punch_success = _perform_daily_punch(client, _request)
            if punch_success:
                push_notification("萌享社签到通知", f"活跃度未达标但打卡成功！")
            else:
                push_notification("萌享社签到通知", f"活跃度未达标且打卡失败。")
        elif is_punch_button_enabled:
            print("活跃度已达标，准备执行每日打卡。")
            punch_success = _perform_daily_punch(client, _request)
            if punch_success:
                push_notification("萌享社签到通知", f"活跃度已达标，每日打卡成功！")
            else:
                push_notification("萌享社签到通知", f"活跃度已达标但每日打卡失败。")
        else:
            print("打卡按钮已禁用，跳过打卡。")

        # --- 最终报告 MB 和活跃度 ---
        mb_final, activity_final = _get_current_mb_and_activity(client, _request)
        final_message = f"最终 MB: {mb_final}，最终活跃度: {activity_final}"
        print(f"\n--- 脚本执行结束 ---")
        print(final_message)
        push_notification("萌享社签到通知", f"脚本运行结束。\n{final_message}")  # 最终结果通知

        return True


def retrieve_cookies_from_fetch(env: str) -> dict:
    from flaresolverr import FlareSolverrHTTPClient  # 确保导入
    def parse_fetch(s: str) -> dict:
        ans = {}
        exec(s, {
            'fetch': lambda _, o: ans.update(o),
            'null': None,
            'undefined': None,
            'true': True,
            'false': False,
        })
        return ans

    fetch_command_string = os.getenv(env)
    if not fetch_command_string:
        raise ValueError(f"Environment variable '{env}' is not set.")

    parsed_data = parse_fetch(fetch_command_string)
    headers_data = parsed_data.get('headers')
    if not headers_data or 'cookie' not in headers_data:
        raise KeyError("Parsed fetch command does not contain expected 'headers' or 'cookie' key.")

    cookie_str = headers_data['cookie']
    return dict(s.strip().split('=', maxsplit=1) for s in cookie_str.split(';'))


def main():
    print("--- 脚本启动 ---")
    env_name = 'MOESHARE_DAYSIGN'

    cookies = {}
    script_successful = False  # 用于标记脚本是否整体成功完成

    try:
        if os.getenv(env_name):
            try:
                cookies = retrieve_cookies_from_fetch(env_name)
                print("--- 成功从环境变量解析出 Cookie ---")
                print("正在尝试执行签到和自动回帖流程...")
                script_successful = daysign(cookies=cookies, flaresolverr_url=os.getenv('FLARESOLVERR_URL'))

            except Exception as e:
                error_msg = f"ERROR: 在 main 函数中处理 {env_name} 环境变量或执行 daysign 时发生错误: {e}"
                print(error_msg)
                traceback.print_exc()
                push_notification("萌享社签到通知", f"脚本运行异常：\n{error_msg}")
        else:
            info_msg = f"INFO: 环境变量 '{env_name}' 未设置。请配置你的 fetch 命令。"
            print(info_msg)
            push_notification("萌享社签到通知", f"脚本运行失败：\n{info_msg}")

    except Exception as e:
        error_msg = f"ERROR: 脚本主函数发生未捕获的错误: {e}"
        print(error_msg)
        traceback.print_exc()
        push_notification("萌享社签到通知", f"脚本运行异常：\n{error_msg}")

    print("--- 脚本执行结束 ---")


if __name__ == '__main__':
    main()