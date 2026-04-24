import time
import requests
import json
import os
import sys
import base64
from config_manager import ConfigManager

# imgbb 图床配置
IMGBB_API_KEY = "9538dee1226cc26d09ad1cc5a8b7d89a"
IMGBB_UPLOAD_URL = "https://api.imgbb.com/1/upload"
# 图床图片自动删除时间（秒）：6小时 = 21600 秒
# 只影响图床服务器上的副本，不影响本地画布展示的图片
IMGBB_EXPIRATION = 21600


# ════════════════════════════════════════════════════════════════
#  模型名称映射（内部 ID → API 调用名）
# ════════════════════════════════════════════════════════════════
# 两个平台的模型名不同，需要分别映射

# Kie.ai 平台模型名映射
KIE_MODEL_NAME_MAP = {
    # Seedream 系列 - Kie.ai 专用
    "seedream-4.5":                    "seedream/4.5-edit",
    "seedream-4.5-edit":               "seedream/4.5-edit",
    "seedream-5.0-lite":               "seedream/5-lite-image-to-image",
    "seedream-5.0-lite-image-to-image":"seedream/5-lite-image-to-image",
    # GPT Image 系列 - Kie.ai 格式
    "gpt-image-2":                     "gpt-image-2-image-to-image",
    "gpt-image-2-text-to-image":       "gpt-image-2-text-to-image",
    "gpt-image-2-image-to-image":      "gpt-image-2-image-to-image",
    # NanoBanana 系列 - 直接透传
    "nano-banana-pro":                 "nano-banana-pro",
    "nano-banana-2":                   "nano-banana-2",
}

# Grsai 平台模型名映射
GRSAI_MODEL_NAME_MAP = {
    # GPT Image 系列 - Grsai 格式（与内部ID相同）
    "gpt-image-2":                     "gpt-image-2",
    "gpt-image-2-text-to-image":       "gpt-image-2",
    "gpt-image-2-image-to-image":      "gpt-image-2",
    # NanoBanana 系列 - 直接透传
    "nano-banana-pro":                 "nano-banana-pro",
    "nano-banana-2":                   "nano-banana-2",
}

def get_kie_model_name(model_id):
    """将内部模型 ID 转换为 Kie.ai API 接受的模型名称"""
    return KIE_MODEL_NAME_MAP.get(model_id, model_id)

def get_grsai_model_name(model_id):
    """将内部模型 ID 转换为 Grsai API 接受的模型名称"""
    return GRSAI_MODEL_NAME_MAP.get(model_id, model_id or "nano-banana-pro")


def upload_to_imgbb(image_path):
    """将本地图片上传到 imgbb 图床，返回公网 URL（图床图片 6 小时后自动删除）"""
    if not image_path or not os.path.isfile(image_path):
        return None

    # 如果已经是 URL，直接返回
    if image_path.startswith("http://") or image_path.startswith("https://"):
        return image_path

    try:
        with open(image_path, "rb") as f:
            img_data = base64.b64encode(f.read()).decode("utf-8")

        resp = requests.post(
            IMGBB_UPLOAD_URL,
            data={
                "key": IMGBB_API_KEY,
                "image": img_data,
                "expiration": IMGBB_EXPIRATION,   # 6小时后图床服务器自动删除副本
            },
            timeout=60
        )
        result = resp.json()

        if result.get("success") and result.get("data", {}).get("url"):
            return result["data"]["url"]
        else:
            raise Exception(f"imgbb 上传失败: {result.get('error', {}).get('message', '未知错误')}")
    except requests.RequestException as e:
        raise Exception(f"imgbb 网络请求失败: {str(e)}")


class KieAIDriver:
    def generate_image(self, prompt, image_input=None, aspect_ratio="1:1", resolution="1K", callback=None, mask_coords=None, model=None):
        """★ v9.13: 支持 model 参数，根据模型和平台自动选择 API 端点。
        
        平台绑定：
        - Kie.ai (kie): Seedream 4.5/5.0、Nano Banana 系列、GPT Image 系列
        - Grsai (grsai): Nano Banana 系列 → /nano-banana 端点
                     GPT Image 系列 → /completions 端点
        """
        config = ConfigManager()
        provider = config.get("api_provider", "kie")
        
        # 默认模型（兼容旧调用）
        if model is None:
            model = "nano-banana-pro"

        # 如果有本地图片，先上传到 imgbb 图床获取公网 URL
        image_url = None
        if image_input:
            if image_input.startswith("http://") or image_input.startswith("https://"):
                image_url = image_input
            else:
                if callback: callback("正在上传参考图到图床...", 5)
                image_url = upload_to_imgbb(image_input)
                if callback: callback("参考图上传成功，开始生成...", 8)

        if provider == "grsai":
            return self._generate_grsai(prompt, image_url, aspect_ratio, resolution, callback, mask_coords, model)
        else:
            return self._generate_kie(prompt, image_url, aspect_ratio, resolution, callback, mask_coords, model)

    def generate_image_multi(self, prompt, image_paths, aspect_ratio="1:1", resolution="1K", callback=None, mask_coords=None, model=None):
        """
        ★ v9.13: 多图合并生成，支持自定义模型。
        image_paths: 按框选顺序排列的本地图片路径列表
        """
        config = ConfigManager()
        provider = config.get("api_provider", "kie")

        # 上传所有图片到 imgbb，获取公网 URL 列表
        urls = []
        for i, path in enumerate(image_paths, 1):
            if callback: callback(f"正在上传第 {i}/{len(image_paths)} 张图片...", 5 + i * 3)
            if path.startswith("http://") or path.startswith("https://"):
                urls.append(path)
            else:
                url = upload_to_imgbb(path)
                urls.append(url)
                print(f"[合并生成] 图{i} 上传完成: {url}")

        if callback: callback("所有图片上传完成，开始合并生成...", 30)

        # 构建带序号的提示词
        numbered_prompt = prompt
        if len(image_paths) > 1:
            order_desc = "、".join([f"图{i}" for i in range(1, len(image_paths) + 1)])
            numbered_prompt = f"（参考素材按顺序：{order_desc}）{prompt}"
            print(f"[合并生成] 增强提示词: {numbered_prompt}")

        if provider == "grsai":
            return self._generate_grsai_multi(numbered_prompt, urls, aspect_ratio, resolution, callback, model)
        else:
            return self._generate_kie_multi(numbered_prompt, urls, aspect_ratio, resolution, callback, model)

    def _generate_kie_multi(self, prompt, image_urls, aspect_ratio, resolution, callback, model=None):
        """★ v9.13: Kie.ai 多图合并生成，支持自定义模型。"""
        config = ConfigManager()
        api_key = config.get("api_key", "")
        if not api_key:
            raise Exception("API Key 未配置，请先在左下角设置中配置。")

        base_url = "https://api.kie.ai"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }

        # ★ v9.14: Kie.ai 需要完整格式的模型名
        kie_model = get_kie_model_name(model or "nano-banana-pro")
        payload = {
            "model": kie_model,
            "input": {
                "prompt": prompt,
                "aspect_ratio": aspect_ratio if aspect_ratio != "auto" else "1:1",
                "resolution": resolution,
                "output_format": "png",
                "image_input": image_urls
            }
        }

        create_url = f"{base_url}/api/v1/jobs/createTask"
        try:
            print(f"[Kie合并] 创建任务: {create_url}")
            print(f"[Kie合并] 图片数: {len(image_urls)}")
            resp = requests.post(create_url, headers=headers, json=payload, timeout=30)
            print(f"[Kie合并] 创建任务响应 ({resp.status_code}): {resp.text[:300]}")
            resp_data = resp.json()
        except requests.Timeout:
            raise Exception("Kie 创建任务超时（30s），请检查网络")
        except Exception as e:
            raise Exception(f"Kie网络请求失败: {str(e)}")

        if resp_data.get("code") != 200:
            raise Exception(f"Kie创建任务失败: {resp_data.get('msg')}")

        task_id = resp_data["data"]["taskId"]
        if callback: callback("Kie线路：合并任务已创建，排队中...", 35)

        query_url = f"{base_url}/api/v1/jobs/recordInfo"
        while True:
            time.sleep(3)
            try:
                q_resp = requests.get(query_url, headers=headers, params={"taskId": task_id}, timeout=20)
                q_data = q_resp.json()
            except requests.Timeout:
                if callback: callback("查询超时，重试中...", 50)
                continue
            except Exception as e:
                if callback: callback(f"查询状态异常: {str(e)}，重试中...", 50)
                continue

            if q_data.get("code") != 200:
                raise Exception(f"查询任务失败: {q_data.get('msg')}")

            state = q_data["data"]["state"]
            if state == "success":
                if callback: callback("生成成功，正在下载原图...", 90)
                result_json_str = q_data["data"].get("resultJson", "{}")
                result_json = json.loads(result_json_str)
                urls = result_json.get("resultUrls", [])
                if not urls:
                    raise Exception("成功状态但未返回图片URL")
                return self._download_image(urls[0], task_id, callback)
            elif state == "fail":
                raise Exception("任务生成失败，请检查提示词或参数。")
            else:
                if callback: callback(f"当前状态: {state}...", 50)

    def _generate_grsai_multi(self, prompt, image_urls, aspect_ratio, resolution, callback, model=None):
        """★ v9.13: Grsai 多图合并生成，自动根据模型选择端点。"""
        config = ConfigManager()
        api_key = config.get("api_key", "")
        if not api_key:
            raise Exception("API Key 未配置，请先在左下角设置中配置。")

        base_url = "https://grsai.dakka.com.cn"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }

        # ★ v9.13: 根据模型决定端点
        if model and model.startswith("gpt-image"):
            create_url = f"{base_url}/v1/draw/completions"
            payload = {
                "model": get_grsai_model_name(model),
                "prompt": prompt,
                "size": aspect_ratio if aspect_ratio != "auto" else "1:1",
                "urls": image_urls,
                "webHook": "-1",
                "shutProgress": False
            }
        else:
            create_url = f"{base_url}/v1/draw/nano-banana"
            payload = {
                "model": get_grsai_model_name(model),
                "prompt": prompt,
                "aspectRatio": aspect_ratio,
                "imageSize": resolution,
                "urls": image_urls,
                "webHook": "-1",
                "shutProgress": True
            }
        try:
            print(f"[Grsai合并] 创建任务: {create_url}")
            print(f"[Grsai合并] 图片数: {len(image_urls)}")
            print(f"[Grsai合并] Prompt: {prompt[:100]}")
            resp = requests.post(create_url, headers=headers, json=payload, timeout=30)
            print(f"[Grsai合并] 创建任务响应 ({resp.status_code}): {resp.text[:300]}")
            resp_data = resp.json()
        except requests.Timeout:
            raise Exception("Grsai 创建任务超时（30s），请检查网络")
        except Exception as e:
            raise Exception(f"Grsai网络请求失败: {str(e)}")

        if resp_data.get("code") != 0:
            raise Exception(f"Grsai创建任务失败: {resp_data.get('msg')}")

        task_id = resp_data["data"]["id"]
        if callback: callback("Grsai线路：合并任务已创建，排队中...", 35)

        query_url = f"{base_url}/v1/draw/result"
        query_payload = {"id": task_id}

        while True:
            time.sleep(3)
            try:
                q_resp = requests.post(query_url, headers=headers, json=query_payload, timeout=20)
                q_data = q_resp.json()
            except requests.Timeout:
                if callback: callback("查询超时，重试中...", 50)
                continue
            except Exception as e:
                if callback: callback(f"查询状态异常: {str(e)}，重试中...", 50)
                continue

            if q_data.get("code") != 0:
                if q_data.get("code") == -2:
                    if callback: callback("任务排队中或尚未生成...", 30)
                    continue
                raise Exception(f"查询任务失败: {q_data.get('msg')}")

            task_info = q_data.get("data", {})
            state = task_info.get("status")
            progress = task_info.get("progress", 50)

            print(f"[Grsai合并] 状态: {state}, 进度: {progress}")
            if state in ("succeed", "succeeded"):
                if callback: callback("生成成功，正在下载原图...", 90)
                results = task_info.get("results", [])
                if not results or not results[0].get("url"):
                    raise Exception("成功状态但未返回图片URL")
                return self._download_image(results[0]["url"], task_id, callback)
            elif state in ("failed", "fail"):
                reason = task_info.get("failure_reason", "")
                error_detail = task_info.get("error", "")
                raise Exception(f"任务生成失败: {reason} - {error_detail}")
            else:
                if callback: callback(f"当前进度: {progress}%...", progress)

    def _generate_kie(self, prompt, image_input, aspect_ratio, resolution, callback, mask_coords=None, model=None):
        """★ v9.13: Kie.ai 单图生成，支持自定义模型。"""
        config = ConfigManager()
        api_key = config.get("api_key", "")
        if not api_key:
            raise Exception("API Key 未配置，请先在左下角设置中配置。")

        base_url = "https://api.kie.ai"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }

        # ★ Kie.ai API 格式：根据官方 curl 示例构造
        kie_model = get_kie_model_name(model or "nano-banana-pro")
        payload = {
            "model": kie_model,
            "input": {
                "prompt": prompt,
                "aspect_ratio": aspect_ratio if aspect_ratio != "auto" else "1:1",
            }
        }
        
        # 根据模型类型决定使用哪个图片字段和额外参数
        if kie_model.startswith("gpt-image"):
            # GPT Image 系列
            if image_input:
                payload["input"]["input_urls"] = [image_input]
        elif kie_model.startswith("seedream"):
            # Seedream 系列 - 需要 quality 和 nsfw_checker
            payload["input"]["quality"] = "basic"
            payload["input"]["nsfw_checker"] = True
            if image_input:
                payload["input"]["image_urls"] = [image_input]
        else:
            # NanoBanana 等其他模型
            if image_input:
                payload["input"]["image_urls"] = [image_input]
        
        if mask_coords:
            payload["input"]["mask_coords"] = mask_coords

        create_url = f"{base_url}/api/v1/jobs/createTask"
        try:
            print(f"[Kie] 创建任务: {create_url}")
            print(f"[Kie] Payload: {json.dumps(payload, ensure_ascii=False)}")
            resp = requests.post(create_url, headers=headers, json=payload, timeout=30)
            print(f"[Kie] 创建任务响应 ({resp.status_code}): {resp.text[:300]}")
            resp_data = resp.json()
        except requests.Timeout:
            raise Exception("Kie 创建任务超时（30s），请检查网络")
        except Exception as e:
            raise Exception(f"Kie网络请求失败: {str(e)}")

        if resp_data.get("code") != 200:
            raise Exception(f"Kie创建任务失败: {resp_data.get('msg')}")

        task_id = resp_data["data"]["taskId"]
        if callback: callback("Kie线路：任务已创建，排队中...", 10)

        query_url = f"{base_url}/api/v1/jobs/recordInfo"
        while True:
            time.sleep(3)
            try:
                q_resp = requests.get(query_url, headers=headers, params={"taskId": task_id}, timeout=20)
                print(f"[Kie] 查询状态响应 ({q_resp.status_code}): {q_resp.text[:300]}")
                q_data = q_resp.json()
            except requests.Timeout:
                if callback: callback("查询超时，重试中...", 50)
                continue
            except Exception as e:
                if callback: callback(f"查询状态异常: {str(e)}，重试中...", 50)
                continue

            if q_data.get("code") != 200:
                raise Exception(f"查询任务失败: {q_data.get('msg')}")

            state = q_data["data"]["state"]
            if state == "success":
                if callback: callback("生成成功，正在下载原图...", 90)
                result_json_str = q_data["data"].get("resultJson", "{}")
                result_json = json.loads(result_json_str)
                urls = result_json.get("resultUrls", [])
                if not urls:
                    raise Exception("成功状态但未返回图片URL")
                
                return self._download_image(urls[0], task_id, callback)
            elif state == "fail":
                raise Exception("任务生成失败，请检查提示词或参数。")
            else:
                if callback: callback(f"当前状态: {state}...", 50)

    def _generate_grsai(self, prompt, image_input, aspect_ratio, resolution, callback, mask_coords=None, model=None):
        """★ v9.13: Grsai 单图生成，自动根据模型选择端点。
        
        - nano-banana 系列 → /v1/draw/nano-banana
        - gpt-image-2       → /v1/draw/completions
        """
        config = ConfigManager()
        api_key = config.get("api_key", "")
        if not api_key:
            raise Exception("API Key 未配置，请先在左下角设置中配置。")

        base_url = "https://grsai.dakka.com.cn"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }

        # ★ v9.13: 根据模型决定端点和参数格式
        if model and model.startswith("gpt-image"):
            # GPT Image 系列 → completions 端点，参数用 size
            create_url = f"{base_url}/v1/draw/completions"
            payload = {
                "model": get_grsai_model_name(model),
                "prompt": prompt,
                "size": aspect_ratio if aspect_ratio != "auto" else "1:1",
                "webHook": "-1",   # 不用 webhook，轮询获取结果
                "shutProgress": False
            }
            if image_input:
                payload["urls"] = [image_input]
            if mask_coords:
                payload["mask_coords"] = mask_coords
        else:
            # Nano Banana 系列 → nano-banana 端点，参数用 aspectRatio/imageSize
            create_url = f"{base_url}/v1/draw/nano-banana"
            payload = {
                "model": get_grsai_model_name(model),
                "prompt": prompt,
                "aspectRatio": aspect_ratio,
                "imageSize": resolution,
                "webHook": "-1",
                "shutProgress": True
            }
            if image_input:
                payload["urls"] = [image_input]
            if mask_coords:
                payload["mask_coords"] = mask_coords
        try:
            print(f"[Grsai] 创建任务: {create_url}")
            print(f"[Grsai] Payload: {json.dumps(payload, ensure_ascii=False)}")
            resp = requests.post(create_url, headers=headers, json=payload, timeout=30)
            print(f"[Grsai] 创建任务响应 ({resp.status_code}): {resp.text[:300]}")
            resp_data = resp.json()
        except requests.Timeout:
            raise Exception("Grsai 创建任务超时（30s），请检查网络")
        except Exception as e:
            raise Exception(f"Grsai网络请求失败: {str(e)}")

        if resp_data.get("code") != 0:
            raise Exception(f"Grsai创建任务失败: {resp_data.get('msg')}")

        task_id = resp_data["data"]["id"]
        if callback: callback("Grsai线路：任务已创建，排队中...", 10)

        query_url = f"{base_url}/v1/draw/result"
        query_payload = {"id": task_id}
        
        while True:
            time.sleep(3)
            try:
                q_resp = requests.post(query_url, headers=headers, json=query_payload, timeout=20)
                print(f"[Grsai] 查询状态响应 ({q_resp.status_code}): {q_resp.text[:300]}")
                q_data = q_resp.json()
            except requests.Timeout:
                if callback: callback("查询超时，重试中...", 50)
                continue
            except Exception as e:
                if callback: callback(f"查询状态异常: {str(e)}，重试中...", 50)
                continue

            if q_data.get("code") != 0:
                if q_data.get("code") == -2:
                    if callback: callback("任务排队中或尚未生成...", 30)
                    continue
                raise Exception(f"查询任务失败: {q_data.get('msg')}")

            task_info = q_data.get("data", {})
            state = task_info.get("status")
            progress = task_info.get("progress", 50)
            
            print(f"[Grsai] 状态: {state}, 进度: {progress}")
            # 轮询接口返回 "succeeded"（带d），流式返回 "succeed"（不带d），两种都兼容
            if state in ("succeed", "succeeded"):
                if callback: callback("生成成功，正在下载原图...", 90)
                results = task_info.get("results", [])
                if not results or not results[0].get("url"):
                    raise Exception("成功状态但未返回图片URL")
                
                return self._download_image(results[0]["url"], task_id, callback)
            elif state in ("failed", "fail"):
                reason = task_info.get("failure_reason", "")
                error_detail = task_info.get("error", "")
                raise Exception(f"任务生成失败: {reason} - {error_detail}")
            else:
                if callback: callback(f"当前进度: {progress}%...", progress)

    def generate_image_with_model(self, prompt, image_paths, aspect_ratio="1:1", resolution="2K", model="nano-banana-pro", callback=None):
        """
        工作流批量生成：支持自定义模型名称 + 多图输入。
        image_paths: 本地图片路径列表（按模板卡槽顺序）
        model: 模型名称字符串
        返回本地图片路径
        """
        config = ConfigManager()
        provider = config.get("api_provider", "kie")
        api_key = config.get("api_key", "")
        if not api_key:
            raise Exception("API Key 未配置，请先在左下角设置中配置。")

        # 上传所有图片到 imgbb
        urls = []
        for i, path in enumerate(image_paths, 1):
            if callback:
                callback(f"正在上传第 {i}/{len(image_paths)} 张图片...", 5 + i * 3)
            if path.startswith("http://") or path.startswith("https://"):
                urls.append(path)
            else:
                url = upload_to_imgbb(path)
                urls.append(url)
                print(f"[工作流生成] 图{i} 上传完成: {url}")

        if callback:
            callback("所有图片上传完成，开始生成...", 30)

        # 构建带序号的提示词
        if len(image_paths) > 1:
            order_desc = "、".join([f"图{i}" for i in range(1, len(image_paths) + 1)])
            numbered_prompt = f"（参考素材按顺序：{order_desc}）{prompt}"
        else:
            numbered_prompt = prompt

        if provider == "grsai":
            return self._gen_model_grsai(numbered_prompt, urls, aspect_ratio, resolution, model, api_key, callback)
        else:
            return self._gen_model_kie(numbered_prompt, urls, aspect_ratio, resolution, model, api_key, callback)

    def _gen_model_kie(self, prompt, image_urls, aspect_ratio, resolution, model, api_key, callback):
        """Kie.ai 自定义模型生成（工作流路径）"""
        base_url = "https://api.kie.ai"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        # ★ Kie.ai API 格式：根据官方 curl 示例构造（与 _generate_kie 保持一致）
        kie_model = get_kie_model_name(model)
        payload = {
            "model": kie_model,
            "input": {
                "prompt": prompt,
                "aspect_ratio": aspect_ratio if aspect_ratio != "auto" else "1:1",
            }
        }
        
        # 根据模型类型决定使用哪个图片字段和额外参数（与 _generate_kie 同步）
        if kie_model.startswith("gpt-image"):
            # GPT Image 系列
            if image_urls:
                payload["input"]["input_urls"] = image_urls if isinstance(image_urls, list) else [image_urls]
        elif kie_model.startswith("seedream"):
            # Seedream 系列 - 需要 quality 和 nsfw_checker
            payload["input"]["quality"] = "basic"
            payload["input"]["nsfw_checker"] = True
            if image_urls:
                payload["input"]["image_urls"] = image_urls if isinstance(image_urls, list) else [image_urls]
        else:
            # NanoBanana 等其他模型
            if image_urls:
                payload["input"]["image_urls"] = image_urls if isinstance(image_urls, list) else [image_urls]

        create_url = f"{base_url}/api/v1/jobs/createTask"
        try:
            print(f"[Kie工作流] 创建任务, 模型: {kie_model}")
            resp = requests.post(create_url, headers=headers, json=payload, timeout=30)
            resp_data = resp.json()
        except requests.Timeout:
            raise Exception("Kie 创建任务超时（30s）")
        except Exception as e:
            raise Exception(f"Kie 网络请求失败: {str(e)}")

        if resp_data.get("code") != 200:
            raise Exception(f"Kie 创建任务失败: {resp_data.get('msg')}")

        task_id = resp_data["data"]["taskId"]
        if callback:
            callback("任务已创建，排队中...", 35)

        query_url = f"{base_url}/api/v1/jobs/recordInfo"
        while True:
            time.sleep(3)
            try:
                q_resp = requests.get(query_url, headers=headers, params={"taskId": task_id}, timeout=20)
                q_data = q_resp.json()
            except requests.Timeout:
                if callback:
                    callback("查询超时，重试中...", 50)
                continue
            except Exception:
                if callback:
                    callback("查询异常，重试中...", 50)
                continue

            if q_data.get("code") != 200:
                raise Exception(f"查询任务失败: {q_data.get('msg')}")

            state = q_data["data"]["state"]
            if state == "success":
                if callback:
                    callback("生成成功，正在下载...", 90)
                result_json_str = q_data["data"].get("resultJson", "{}")
                result_json = json.loads(result_json_str)
                urls = result_json.get("resultUrls", [])
                if not urls:
                    raise Exception("成功状态但未返回图片URL")
                return self._download_image(urls[0], task_id, callback)
            elif state == "fail":
                raise Exception("任务生成失败")
            else:
                if callback:
                    callback(f"当前状态: {state}...", 50)

    def _gen_model_grsai(self, prompt, image_urls, aspect_ratio, resolution, model, api_key, callback):
        """Grsai 自定义模型生成"""
        base_url = "https://grsai.dakka.com.cn"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": model,
            "prompt": prompt,
            "aspectRatio": aspect_ratio,
            "imageSize": resolution,
            "urls": image_urls,
            "webHook": "1",
            "shutProgress": True
        }
        create_url = f"{base_url}/v1/draw/nano-banana"
        try:
            print(f"[Grsai工作流] 创建任务, 模型: {model}")
            resp = requests.post(create_url, headers=headers, json=payload, timeout=30)
            resp_data = resp.json()
        except requests.Timeout:
            raise Exception("Grsai 创建任务超时")
        except Exception as e:
            raise Exception(f"Grsai 网络请求失败: {str(e)}")

        if resp_data.get("code") != 0:
            raise Exception(f"Grsai 创建任务失败: {resp_data.get('msg')}")

        task_id = resp_data["data"]["id"]
        if callback:
            callback("任务已创建，排队中...", 35)

        query_url = f"{base_url}/v1/draw/result"
        query_payload = {"id": task_id}
        while True:
            time.sleep(3)
            try:
                q_resp = requests.post(query_url, headers=headers, json=query_payload, timeout=20)
                q_data = q_resp.json()
            except requests.Timeout:
                if callback:
                    callback("查询超时，重试中...", 50)
                continue
            except Exception:
                if callback:
                    callback("查询异常，重试中...", 50)
                continue

            if q_data.get("code") != 0:
                if q_data.get("code") == -2:
                    if callback:
                        callback("排队中...", 30)
                    continue
                raise Exception(f"查询任务失败: {q_data.get('msg')}")

            task_info = q_data.get("data", {})
            state = task_info.get("status")
            progress = task_info.get("progress", 50)

            if state in ("succeed", "succeeded"):
                if callback:
                    callback("生成成功，正在下载...", 90)
                results = task_info.get("results", [])
                if not results or not results[0].get("url"):
                    raise Exception("成功状态但未返回图片URL")
                return self._download_image(results[0]["url"], task_id, callback)
            elif state in ("failed", "fail"):
                reason = task_info.get("failure_reason", "")
                raise Exception(f"任务生成失败: {reason}")
            else:
                if callback:
                    callback(f"当前进度: {progress}%...", progress)

    def _download_image(self, img_url, task_id, callback):
        """下载图片到本地 outputs 目录"""
        print(f"[下载] 开始下载图片: {img_url}")
        img_resp = requests.get(img_url, timeout=60)
        print(f"[下载] HTTP状态: {img_resp.status_code}, 内容大小: {len(img_resp.content)} 字节")

        # 确定输出目录
        if getattr(sys, 'frozen', False):
            base_dir = os.path.dirname(os.path.abspath(sys.executable))
        else:
            base_dir = os.path.dirname(os.path.abspath(sys.argv[0]))

        output_dir = os.path.join(base_dir, "outputs")
        os.makedirs(output_dir, exist_ok=True)
        local_path = os.path.join(output_dir, f"{task_id}.png")
        print(f"[下载] 保存路径: {local_path}")

        with open(local_path, "wb") as f:
            f.write(img_resp.content)

        if not os.path.isfile(local_path):
            raise Exception(f"图片下载后文件不存在: {local_path}")

        print(f"[下载] 保存成功，文件大小: {os.path.getsize(local_path)} 字节")
        if callback:
            callback("完成！", 100)
        return local_path
