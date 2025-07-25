import os
import re
import requests
import concurrent.futures
from urllib.parse import urlparse
import sys
import time
from tqdm import tqdm

class GradleDownloader:
    def __init__(self, gradle_file, max_workers=5):
        self.gradle_file = gradle_file
        self.max_workers = max_workers
        self.download_dir = os.path.expanduser('~/.gradle/caches/modules-2/files-2.1')
        self.chunk_size = 1024 * 1024  # 1MB chunks
        
        # 获取系统代理设置
        self.proxies = {
            'http': os.environ.get('http_proxy') or os.environ.get('HTTP_PROXY'),
            'https': os.environ.get('https_proxy') or os.environ.get('HTTPS_PROXY')
        }
        
        # 创建下载目录
        os.makedirs(self.download_dir, exist_ok=True)

    def parse_gradle_files(self):
        """解析gradle文件中的依赖"""
        urls = set()
        
        # 读取gradle文件
        with open(self.gradle_file, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # 查找Kotlin DSL格式的implementation声明
        dependencies = re.findall(r'implementation\("([^"]+)"\)', content)
        # 查找plugins块中的依赖
        plugin_deps = re.findall(r'id\("([^"]+)"\)\s+version\s+"([^"]+)"', content)
        
        # 转换成maven格式的URL
        maven_repos = [
            "https://maven.aliyun.com/repository/public",
            "https://maven.aliyun.com/repository/google",
            "https://maven.aliyun.com/repository/gradle-plugin",
            "https://maven.aliyun.com/repository/jcenter",
            "https://dl.google.com/dl/android/maven2",
            "https://plugins.gradle.org/m2"
        ]
        
        # 添加特定的版本映射和额外依赖
        version_mappings = {
            "com.android.tools.build:gradle": "7.4.2",  # 降级到更稳定的版本
            "org.jetbrains.kotlin:kotlin-gradle-plugin": "1.8.0",
            "org.jetbrains.kotlin:kotlin-stdlib": "1.8.0",
            "org.jetbrains.kotlin:kotlin-stdlib-common": "1.8.0",
            "org.jetbrains.kotlin:kotlin-stdlib-jdk8": "1.8.0",
            "org.jetbrains.kotlin:kotlin-stdlib-jdk7": "1.8.0",
            "com.android.tools:common": "30.4.2",
            "com.android.tools:sdk-common": "30.4.2",
            "com.android.tools:sdklib": "30.4.2"
        }
        
        # 添加额外的核心依赖
        extra_deps = [
            "org.jetbrains.kotlin:kotlin-stdlib:1.8.0",
            "org.jetbrains.kotlin:kotlin-stdlib-common:1.8.0",
            "org.jetbrains.kotlin:kotlin-stdlib-jdk8:1.8.0",
            "org.jetbrains.kotlin:kotlin-stdlib-jdk7:1.8.0",
            "com.android.tools:common:30.4.2",
            "com.android.tools:sdk-common:30.4.2",
            "com.android.tools:sdklib:30.4.2"
        ]
        
        # 添加额外依赖到下载列表
        for dep in extra_deps:
            dependencies.append(dep)
        
        # 处理常规依赖
        for dep in dependencies:
            if dep.count(':') >= 2:
                group_id, artifact_id, version = dep.split(':')[:3]
                group_path = group_id.replace('.', '/')
                
                for repo in maven_repos:
                    # 添加jar文件
                    url = f"{repo}/{group_path}/{artifact_id}/{version}/{artifact_id}-{version}.jar"
                    urls.add(url)
                    # 添加aar文件（Android库通常是aar格式）
                    url_aar = f"{repo}/{group_path}/{artifact_id}/{version}/{artifact_id}-{version}.aar"
                    urls.add(url_aar)
                    # 添加pom文件
                    url_pom = f"{repo}/{group_path}/{artifact_id}/{version}/{artifact_id}-{version}.pom"
                    urls.add(url_pom)
        
        # 处理插件依赖
        for plugin_id, version in plugin_deps:
            # 转换插件ID为Maven坐标
            if plugin_id == "com.android.application":
                group_id = "com.android.tools.build"
                artifact_id = "gradle"
                maven_key = f"{group_id}:{artifact_id}"
                version = version_mappings.get(maven_key, version)  # 使用映射版本
            elif plugin_id == "kotlin-android":
                group_id = "org.jetbrains.kotlin"
                artifact_id = "kotlin-gradle-plugin"
                maven_key = f"{group_id}:{artifact_id}"
                version = version_mappings.get(maven_key, version)  # 使用映射版本
            else:
                continue
                
            group_path = group_id.replace('.', '/')
            
            for repo in maven_repos:
                # 添加jar文件
                url = f"{repo}/{group_path}/{artifact_id}/{version}/{artifact_id}-{version}.jar"
                urls.add(url)
                # 添加pom文件
                url_pom = f"{repo}/{group_path}/{artifact_id}/{version}/{artifact_id}-{version}.pom"
                urls.add(url_pom)
                # 添加module文件
                url_module = f"{repo}/{group_path}/{artifact_id}/{version}/{artifact_id}-{version}.module"
                urls.add(url_module)
        
        return urls

    def download_file(self, url):
        """下载单个文件，支持断点续传"""
        file_name = os.path.join(self.download_dir, urlparse(url).path.lstrip('/'))
        os.makedirs(os.path.dirname(file_name), exist_ok=True)
        
        # 检查文件是否已存在
        if os.path.exists(file_name):
            if os.path.getsize(file_name) > 0:  # 确保文件不是空的
                return f"File already exists: {url}"
            else:
                os.remove(file_name)  # 删除空文件
        
        headers = {}
        # 如果文件已经部分下载，设置断点续传
        if os.path.exists(f"{file_name}.temp"):
            temp_size = os.path.getsize(f"{file_name}.temp")
            headers['Range'] = f'bytes={temp_size}-'
        
        try:
            # 禁用警告
            import urllib3
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            
            response = requests.get(url, 
                                 stream=True, 
                                 proxies=self.proxies, 
                                 headers=headers,
                                 verify=False,
                                 timeout=30)
            
            if response.status_code not in [200, 206]:
                return f"Failed to download {url}: {response.status_code}"
            
            total_size = int(response.headers.get('content-length', 0))
            
            mode = 'ab' if 'Range' in headers else 'wb'
            with open(f"{file_name}.temp", mode) as f, tqdm(
                total=total_size,
                initial=temp_size if 'Range' in headers else 0,
                unit='iB',
                unit_scale=True,
                unit_divisor=1024,
                desc=os.path.basename(file_name)
            ) as pbar:
                for chunk in response.iter_content(chunk_size=self.chunk_size):
                    size = f.write(chunk)
                    pbar.update(size)
            
            # 下载完成后重命名文件
            os.rename(f"{file_name}.temp", file_name)
            return f"Successfully downloaded: {url}"
            
        except Exception as e:
            return f"Error downloading {url}: {str(e)}"

    def download_all(self):
        """并发下载所有文件"""
        urls = self.parse_gradle_files()
        print(f"Found {len(urls)} files to download")
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = [executor.submit(self.download_file, url) for url in urls]
            for future in concurrent.futures.as_completed(futures):
                print(future.result())

def main():
    if len(sys.argv) < 2:
        print("Usage: python gradle_downloader.py <path_to_gradle_file>")
        return
    
    gradle_file = sys.argv[1]
    if not os.path.exists(gradle_file):
        print(f"File not found: {gradle_file}")
        return
    
    downloader = GradleDownloader(gradle_file)
    downloader.download_all()

if __name__ == "__main__":
    main()
