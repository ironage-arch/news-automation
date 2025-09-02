import feedparser
import requests
import json
import re
import openai
import os.path
import datetime
import smtplib
import getpass
import urllib.parse
import time
import warnings
import urllib3
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate
from email.header import Header
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from urllib.parse import urlparse, parse_qs
from bs4 import BeautifulSoup # ✨ 라이브러리 추가

# ==============================================================================
# --- 1. 사용자 설정 (GitHub Actions Secrets에서 자동으로 불러옵니다) ---
# ==============================================================================

# Google Alerts RSS 주소
GOOGLE_ALERTS_RSS_URLS = [
    "https://www.google.co.kr/alerts/feeds/14299983816346888060/2091321787487599294", #Satellite Communications
    "https://www.google.co.kr/alerts/feeds/14299983816346888060/7282625974461397688", #위성통신
    "https://www.google.co.kr/alerts/feeds/14299983816346888060/2091321787487600193", #Non terrestrial Networks
    "https://www.google.co.kr/alerts/feeds/14299983816346888060/2091321787487600258", #3GPP
    "https://www.google.co.kr/alerts/feeds/14299983816346888060/6144919849490706746", #6G
    "https://www.google.co.kr/alerts/feeds/14299983816346888060/13972650129806487379", #저궤도
    "https://www.google.co.kr/alerts/feeds/14299983816346888060/5231113795348014351", #FCC 47 CFR PArt 25
    "https://www.google.co.kr/alerts/feeds/14299983816346888060/6144919849490708240", #low Earth orbit
    "https://www.google.co.kr/alerts/feeds/14299983816346888060/12348804382892789873", #주파수
    "https://www.google.co.kr/alerts/feeds/14299983816346888060/6144919849490708655", #AI-RAN
    "https://www.google.co.kr/alerts/feeds/14299983816346888060/270492137594840372", #AI-RAN
    "https://www.google.co.kr/alerts/feeds/14299983816346888060/2496376606356182211", #AI network
    "https://www.google.co.kr/alerts/feeds/14299983816346888060/2496376606356181274", #ITU-R
    "https://www.google.co.kr/alerts/feeds/14299983816346888060/18373922797329225191", #ISAC
    "https://www.google.co.kr/alerts/feeds/14299983816346888060/2496376606356184244", #IMT-2030
]

# Naver 검색 키워드
NAVER_QUERIES = [
    "위성통신", "satellite communication",
    "저궤도", "LEO",
    "ICT 표준", "ICT standardization",
    "주파수 정책", "spectrum policy",
    "3GPP", "ITU", "FCC", "ofcom"
]

# GitHub Secrets를 통해 환경 변수에서 API 키와 설정값 불러오기
NAVER_CLIENT_ID = os.environ.get("NAVER_CLIENT_ID")
NAVER_CLIENT_SECRET = os.environ.get("NAVER_CLIENT_SECRET")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
SENDER_EMAIL = os.environ.get("SENDER_EMAIL")
GMAIL_PASSWORD = os.environ.get("GMAIL_PASSWORD")
RECEIVER_EMAIL = [email.strip() for email in os.environ.get("RECEIVER_EMAIL", "").split(',') if email.strip()]

# Google API 설정
SCOPES = ['https://www.googleapis.com/auth/documents', 'https://www.googleapis.com/auth/drive']

# ==============================================================================
# --- 1. 헬퍼 함수 (✨ 새로워진 버전) ---
# ==============================================================================
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def configure_ssl_warnings(suppress_warnings=True):
    """
    SSL 관련 경고를 제어하는 함수
    
    Args:
        suppress_warnings (bool): True면 경고 억제, False면 경고 표시
    """
    if suppress_warnings:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        warnings.filterwarnings('ignore', message='Unverified HTTPS request')
    else:
        # 경고 다시 활성화하려면
        warnings.resetwarnings()

def extract_google_alerts_url(google_url: str) -> str:
    """
    구글 알리미 RSS의 복잡한 링크에서 실제 뉴스 URL을 추출합니다.
    
    Args:
        google_url (str): 구글 알리미에서 제공하는 원본 링크
        
    Returns:
        str: 추출된 실제 뉴스 URL
    """
    try:
        # 방법 1: &url= 파라미터에서 추출
        if "&url=" in google_url:
            extracted_url = google_url.split("&url=")[1]
            # URL 디코딩
            extracted_url = urllib.parse.unquote(extracted_url)
            # 추가 파라미터 제거 (&sa=U 등)
            if "&" in extracted_url:
                extracted_url = extracted_url.split("&")[0]
            return extracted_url
        
        # 방법 2: q= 파라미터에서 추출 (구글 검색 결과 형태)
        if "q=" in google_url:
            parsed = urlparse(google_url)
            query_params = parse_qs(parsed.query)
            if 'q' in query_params:
                potential_url = query_params['q'][0]
                if potential_url.startswith('http'):
                    return potential_url
        
        # 방법 3: 직접 HTTP인 경우
        if google_url.startswith('http') and 'google.com' not in google_url:
            return google_url
            
        return google_url  # 추출 실패시 원본 반환
        
    except Exception as e:
        print(f"    (경고) URL 추출 실패: {str(e)[:100]}")
        return google_url

def get_final_url_and_source(url: str, max_retries: int = 2) -> tuple:
    """
    리디렉션을 따라가 최종 URL을 찾고 출처를 추출합니다 (재시도 로직 포함)
    
    Args:
        url (str): 추적할 URL
        max_retries (int): 최대 재시도 횟수
        
    Returns:
        tuple: (최종 URL, 추출된 언론사 이름, 성공 여부)
    """
    for attempt in range(max_retries + 1):
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                'Accept-Language': 'ko-KR,ko;q=0.8,en-US;q=0.5,en;q=0.3',
                'Accept-Encoding': 'gzip, deflate',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1',
            }
            
            # 타임아웃 설정을 더 짧게 (SSL 검증 시도 후 실패시 비활성화)
            try:
                # 먼저 SSL 검증 활성화로 시도
                response = requests.get(url, headers=headers, allow_redirects=True, 
                                        timeout=(5, 10), verify=True)
            except requests.exceptions.SSLError:
                # SSL 오류 시 검증 비활성화로 재시도
                response = requests.get(url, headers=headers, allow_redirects=True, 
                                        timeout=(5, 10), verify=False)
            
            # 상태 코드 체크 (404, 403 등도 허용하되 기록)
            if response.status_code >= 400:
                print(f"    (정보) HTTP {response.status_code}: {url[:60]}...")
                # 그래도 URL 파싱은 시도
                
            final_url = response.url
            parsed_url = urlparse(final_url)
            domain = parsed_url.netloc
            
            # 도메인에서 언론사 이름 추출 (더 정교하게)
            domain_clean = domain.replace('www.', '').replace('m.', '')
            source_parts = domain_clean.split('.')
            
            # 한국 언론사 도메인 특별 처리
            source_mapping = {
                'chosun': '조선일보', 'donga': '동아일보', 'joongang': '중앙일보',
                'hankyoreh': '한겨레', 'hani': '한겨레', 'khan': '경향신문',
                'mt': '머니투데이', 'mk': '매일경제', 'seoul': '서울신문',
                'ytn': 'YTN', 'sbs': 'SBS', 'kbs': 'KBS', 'mbc': 'MBC'
            }
            
            source_name = source_mapping.get(source_parts[0].lower(), source_parts[0].capitalize())
            
            return final_url, source_name, True
            
        except requests.exceptions.Timeout:
            print(f"    (재시도 {attempt + 1}/{max_retries + 1}) 타임아웃: {url[:50]}...")
            if attempt < max_retries:
                time.sleep(1)  # 1초 대기 후 재시도
                continue
                
        except requests.exceptions.RequestException as e:
            error_msg = str(e)[:100]
            print(f"    (재시도 {attempt + 1}/{max_retries + 1}) 요청 오류: {error_msg}")
            if attempt < max_retries:
                time.sleep(1)
                continue
                
        except Exception as e:
            print(f"    (경고) 예상치 못한 오류: {str(e)[:100]}")
            break
    
    # 모든 시도 실패시 URL에서 도메인만 추출해서라도 소스 이름 생성
    try:
        parsed = urlparse(url)
        domain = parsed.netloc.replace('www.', '').replace('m.', '')
        fallback_source = domain.split('.')[0].capitalize() if domain else "출처 불명"
        return url, fallback_source, False
    except:
        return url, "출처 불명", False
        

# 💡💡💡 --- [신규] 뉴스 본문 추출 함수 --- 💡💡💡
def get_article_content(url: str, max_length: int = 5000) -> str:
    """
    주어진 URL에서 뉴스 기사 본문을 추출합니다.
    
    Args:
        url (str): 뉴스 기사 URL
        max_length (int): API 토큰 제한을 위해 가져올 최대 글자 수
        
    Returns:
        str: 추출 및 정제된 기사 본문 텍스트
    """
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        response = requests.get(url, headers=headers, timeout=10, verify=False)
        response.raise_for_status()

        # HTML 파싱
        soup = BeautifulSoup(response.text, 'lxml')

        # 불필요한 태그 제거 (스크립트, 스타일, 광고 등)
        for element in soup(["script", "style", "header", "footer", "nav", "aside"]):
            element.decompose()

        # 기사 본문 유력 후보 태그 탐색
        article_body = soup.find('article') or \
                       soup.find('div', id=re.compile(r'content|article|main', re.I)) or \
                       soup.find('main')
        
        if article_body:
            text = article_body.get_text(separator='\n', strip=True)
        else:
            # 후보가 없으면 모든 <p> 태그 텍스트를 조합
            paragraphs = soup.find_all('p')
            text = '\n'.join(p.get_text(strip=True) for p in paragraphs if len(p.get_text(strip=True)) > 50)
            if not text: # 그래도 없으면 body 전체 텍스트 사용
                 text = soup.body.get_text(separator='\n', strip=True)


        # 텍스트 정제
        lines = (line.strip() for line in text.splitlines())
        chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
        cleaned_text = '\n'.join(chunk for chunk in chunks if chunk)
        
        if not cleaned_text:
            return "기사 본문을 추출하지 못했습니다."

        return cleaned_text[:max_length]

    except requests.exceptions.RequestException as e:
        return f"본문 수집 실패 (네트워크 오류): {e}"
    except Exception as e:
        return f"본문 수집 실패 (알 수 없는 오류): {e}"


# ==============================================================================
# --- 2. 개선된 뉴스 수집 함수 (오류 처리 및 통계 추가) ---
# ==============================================================================

def get_news_data():
    """여러 RSS 피드와 키워드에서 뉴스를 수집하고 실제 출처를 표기하는 함수"""
    news_list = []
    failed_urls = []  # 실패한 URL들 추적
    
    # 통계 추적용
    stats = {
        'google_alerts': {'total': 0, 'success': 0, 'failed': 0, 'connection_errors': 0},
        'naver': {'total': 0, 'success': 0, 'failed': 0, 'connection_errors': 0}
    }
    
    print("\n🔍 Google Alerts에서 뉴스를 수집합니다...")
    
    for i, rss_url in enumerate(GOOGLE_ALERTS_RSS_URLS, 1):
        if not rss_url.strip(): 
            continue
            
        print(f"  📡 RSS 피드 {i}/{len(GOOGLE_ALERTS_RSS_URLS)} 처리 중...")
        
        try:
            # RSS 파싱 자체도 타임아웃 설정
            import socket
            old_timeout = socket.getdefaulttimeout()
            socket.setdefaulttimeout(15)  # RSS 파싱 타임아웃 늘림
            
            feed = feedparser.parse(rss_url)
            socket.setdefaulttimeout(old_timeout)
            
            if not hasattr(feed, 'entries') or not feed.entries:
                print(f"    ⚠️  RSS 피드가 비어있거나 파싱 실패")
                continue
                
            print(f"    📰 {len(feed.entries)}개 항목 발견")
            
            # 각 항목을 순차적으로 처리 (안정성 우선)
            for j, entry in enumerate(feed.entries, 1):
                stats['google_alerts']['total'] += 1
                
                print(f"        🔄 항목 {j}/{len(feed.entries)} 처리 중...", end=' ')
                
                try:
                    # 구글 알리미 링크에서 실제 URL 추출
                    extracted_url = extract_google_alerts_url(entry.link)
                    
                    # URL 길이 체크 (너무 긴 URL은 건너뛰기)
                    if len(extracted_url) > 500:
                        print("❌ (URL 너무 김)")
                        stats['google_alerts']['failed'] += 1
                        continue
                    
                    # 최종 URL과 출처 확인
                    final_link, source, success = get_final_url_and_source(extracted_url)
                    
                    if success:
                        stats['google_alerts']['success'] += 1
                        print("✅")
                    else:
                        stats['google_alerts']['failed'] += 1
                        failed_urls.append(extracted_url)
                        # 연결 관련 오류인지 확인
                        if "연결" in str(extracted_url) or "Connection" in str(extracted_url):
                            stats['google_alerts']['connection_errors'] += 1
                        print("❌")
                    
                    # 발행일 처리 개선
                    try:
                        if hasattr(entry, 'published_parsed') and entry.published_parsed:
                            published_date = datetime.datetime(*entry.published_parsed[:6]).strftime('%Y-%m-%d')
                        else:
                            # published_parsed가 없으면 현재 날짜 사용
                            published_date = datetime.datetime.now().strftime('%Y-%m-%d')
                    except Exception as date_error:
                        published_date = datetime.datetime.now().strftime('%Y-%m-%d')
                        print(f"         (날짜 파싱 오류: {date_error})")
                    
                    news_list.append({
                        "title": entry.title,
                        "link": final_link,
                        "published": published_date,
                        "source": source,
                        "extraction_success": success
                    })
                    
                    # 각 요청 사이에 짧은 대기 (서버 부하 방지)
                    time.sleep(0.5)
                    
                except Exception as item_error:
                    stats['google_alerts']['failed'] += 1
                    failed_urls.append(getattr(entry, 'link', 'Unknown URL'))
                    print(f"❌ (오류: {str(item_error)[:50]})")
                    continue
                    
        except Exception as feed_error:
            print(f"  ❌ RSS 피드 전체 처리 실패: {str(feed_error)[:100]}")
            continue

    print(f"\n📊 Google Alerts 통계:")
    print(f"    • 총 처리: {stats['google_alerts']['total']}개")
    print(f"    • 성공: {stats['google_alerts']['success']}개")
    print(f"    • 실패: {stats['google_alerts']['failed']}개")
    if stats['google_alerts']['connection_errors'] > 0:
        print(f"    • 연결 오류: {stats['google_alerts']['connection_errors']}개")

    print("\n🔍 Naver News에서 뉴스를 수집합니다...")
    
    for i, query in enumerate(NAVER_QUERIES, 1):
        if not query.strip(): 
            continue
            
        print(f"  🔍 검색어 {i}/{len(NAVER_QUERIES)}: '{query}'")
        
        try:
            naver_url = "https://openapi.naver.com/v1/search/news.json"
            headers = {
                "X-Naver-Client-Id": NAVER_CLIENT_ID, 
                "X-Naver-Client-Secret": NAVER_CLIENT_SECRET
            }
            params = {"query": query, "display": 20, "sort": "date"}
            
            response = requests.get(naver_url, headers=headers, params=params, timeout=15)
            response.raise_for_status()
            data = response.json()
            
            items = data.get("items", [])
            print(f"    📰 {len(items)}개 발견")
            
            for j, item in enumerate(items, 1):
                stats['naver']['total'] += 1
                
                print(f"        🔄 항목 {j}/{len(items)} 처리 중...", end=' ')
                
                try:
                    clean_title = re.sub('<[^>]*>', '', item["title"])
                    
                    # 날짜 파싱 개선
                    try:
                        published_date = datetime.datetime.strptime(
                            item['pubDate'], '%a, %d %b %Y %H:%M:%S +0900'
                        ).strftime('%Y-%m-%d')
                    except Exception as date_error:
                        published_date = datetime.datetime.now().strftime('%Y-%m-%d')
                
                    raw_link = item.get("originallink", item["link"])
                    
                    # URL 유효성 기본 체크
                    if not raw_link.startswith('http'):
                        print("❌ (잘못된 URL)")
                        stats['naver']['failed'] += 1
                        continue
                    
                    final_link, source, success = get_final_url_and_source(raw_link)
                    
                    if success:
                        stats['naver']['success'] += 1
                        print("✅")
                    else:
                        stats['naver']['failed'] += 1
                        failed_urls.append(raw_link)
                        print("❌")
                    
                    news_list.append({
                        "title": clean_title,
                        "link": final_link,
                        "published": published_date,
                        "source": source,
                        "extraction_success": success
                    })
                    
                    # 네이버도 요청 간 대기
                    time.sleep(0.3)
                    
                except Exception as item_error:
                    stats['naver']['failed'] += 1
                    print(f"❌ (오류: {str(item_error)[:50]})")
                    continue
                    
        except Exception as e:
            print(f"  ❌ 네이버 뉴스 API 실패: {str(e)[:100]}")
            continue

    print(f"\n📊 Naver News 통계:")
    print(f"    • 총 처리: {stats['naver']['total']}개")
    print(f"    • 성공: {stats['naver']['success']}개")
    print(f"    • 실패: {stats['naver']['failed']}개")

    # 실패한 URL 상위 5개 출력 (디버깅용)
    if failed_urls:
        print(f"\n⚠️  실패한 URL 샘플 ({len(failed_urls)}개 중 최대 5개):")
        for i, failed_url in enumerate(failed_urls[:5], 1):
            print(f"    {i}. {failed_url[:80]}...")

    # 중복 제거 및 정렬
    print(f"\n🔄 중복 제거 전: {len(news_list)}개 뉴스")
    
    news_list.sort(key=lambda x: x['published'], reverse=True)
    seen_links = set()
    unique_news_items = []
    
    for item in news_list:
        # URL 정규화 개선
        try:
            normalized_link = re.sub(r'^https?:\/\/(www\.|m\.|amp\.)?', '', item['link']).rstrip('/')
            # 쿼리 파라미터도 제거하여 더 정확한 중복 제거
            normalized_link = normalized_link.split('?')[0].split('#')[0]
            
            if normalized_link not in seen_links:
                unique_news_items.append(item)
                seen_links.add(normalized_link)
        except Exception as e:
            # 정규화 실패해도 일단 추가
            unique_news_items.append(item)
            
    print(f"🎯 중복 제거 후: {len(unique_news_items)}개 뉴스")
    
    # 최종 성공률 계산 및 출력
    total_items = stats['google_alerts']['total'] + stats['naver']['total']
    total_success = stats['google_alerts']['success'] + stats['naver']['success']
    total_failed = stats['google_alerts']['failed'] + stats['naver']['failed']
    
    if total_items > 0:
        success_rate = (total_success / total_items * 100)
        print(f"\n📈 최종 결과:")
        print(f"    • 전체 시도: {total_items}개")
        print(f"    • 성공: {total_success}개 ({success_rate:.1f}%)")
        print(f"    • 실패: {total_failed}개 ({100-success_rate:.1f}%)")
        
        # 성공률이 낮으면 권장사항 출력
        if success_rate < 70:
            print(f"\n💡 성공률 개선 제안:")
            print(f"    • VPN 사용 고려")
            print(f"    • 실행 시간대 변경")
            print(f"    • RSS URL 점검")
    
    return unique_news_items


# ==============================================================================
# --- 3. (신규) AI 뉴스 선별 함수 (로직 구체화) ---
# ==============================================================================
def filter_news_by_ai(news_items):
    """AI를 사용해 정책 입안자에게 가장 관련성 높은 뉴스를 선별하는 함수"""
    print("\n[🚀 작업 중] AI가 정책 입안자를 위해 뉴스를 선별하고 있습니다...")
    if not OPENAI_API_KEY or OPENAI_API_KEY == "YOUR_OPENAI_API_KEY":
        print("  (경고) OpenAI API 키가 없어 뉴스 선별을 건너뛰고 최신 뉴스 20개를 분석합니다.")
        return news_items[:20]

    client = openai.OpenAI(api_key=OPENAI_API_KEY)

    formatted_news_list = ""
    for i, item in enumerate(news_items):
        formatted_news_list += f"{i}: {item['title']}\n"

    prompt = f"""
    당신은 ICT 표준 정책 최고 전문가의 수석 보좌관입니다.
    당신의 임무는 아래 뉴스 목록에서 먼저 내용이 중복되는 기사들을 제거한 뒤, '표준 정책 입안자'의 관점에서 가장 중요한 뉴스 20개를 선별하는 것입니다.

    [작업 절차]
    1. **중복 제거**: 아래 뉴스 목록에서 사실상 동일한 사건이나 주제를 다루는 기사들을 하나의 그룹으로 묶고, 각 그룹에서 가장 포괄적인 대표 기사 하나만 남깁니다.
    2. **최종 선별**: 중복이 제거된 뉴스 목록에서, 아래 [선별 최우선 기준]에 따라 가장 중요한 뉴스 20개를 최종적으로 선별합니다.

    [선별 최우선 기준]
    정책적 중요도를 최우선으로 고려하며, 특히 아래 주제를 다루는 국내외 뉴스에 높은 가중치를 부여합니다.
    - **해외 주요국 정책/규제**: 미국(FCC), 유럽(ETSI) 등 해외 주요국의 ICT 정책, 법안, 규제 변화
    - **국제 표준화 동향**: 3GPP, ITU 등 국제 표준화 기구의 주요 결정 및 논의 사항
    - **국내 정부 계획 및 발표**: 국내 정부 부처가 발표하는 ICT 정책, 법안, 기술 개발 계획
    - **산업계 핵심 동향**: ICT 산업 및 시장 판도에 큰 영향을 미치는 국내외 기업의 기술 개발 및 사업 전략
    - **정책 비판 및 대안**: 현재 정책의 문제점을 지적하거나 새로운 대안을 제시하는 기사


    [뉴스 목록]
    {formatted_news_list}

    [요청]
    위 절차와 기준에 따라 최종적으로 선별된 뉴스의 번호 20개만 쉼표(,)로 구분하여 응답해 주십시오.
    예시: 3, 8, 12, 15, 21, 23, 25, 30, 31, 33, 40, 41, 42, 45, 50
    (설명이나 다른 텍스트는 절대 포함하지 마세요. 번호만 응답해야 합니다.)
    """

    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "당신은 ICT 표준 정책 전문가의 유능한 보좌관입니다. 주어진 뉴스 목록에서 중복을 제거하고, 정책적 중요도가 가장 높은 20개를 골라 번호만 응답합니다."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.0,
        )
        selected_indices_str = response.choices[0].message.content
        print(f"  > AI가 선별한 뉴스 인덱스: {selected_indices_str}")

        selected_indices = [int(i.strip()) for i in selected_indices_str.split(',')]
        
        filtered_news = [news_items[i] for i in selected_indices if i < len(news_items)]
        
        if not filtered_news:
             raise ValueError("AI가 유효한 인덱스를 반환하지 않았습니다.")
            
        return filtered_news

    except Exception as e:
        print(f"  (경고) AI 뉴스 선별 실패: {e}. 최신 뉴스 20개로 대체합니다.")
        return news_items[:20]

# ==============================================================================
# --- 4. AI 심층 분석 함수 (프롬프트 수정) ---
# ==============================================================================
def analyze_news_with_ai(news_item):
    """AI에게 뉴스를 보내 새로운 형식으로 심층 분석을 요청하는 함수"""
    if not OPENAI_API_KEY or OPENAI_API_KEY == "YOUR_OPENAI_API_KEY":
        return "OpenAI API 키가 설정되지 않아 분석을 건너뜁니다."
        
    client = openai.OpenAI(api_key=OPENAI_API_KEY)
    
    # 💡💡💡 --- [수정] 프롬프트에 '뉴스 본문' 추가 --- 💡💡💡
    prompt = f"""
    # Mission
    당신은 주어진 뉴스 기사 1개를 분석하여, ICT 표준·정책 전문가를 위한 '심층 분석 보고서'를 생성하는 AI 애널리스트입니다. 보고서의 모든 내용은 반드시 기사 본문에 명시된 사실, 데이터, 인용에 근거해야 하며, 당신의 사전 지식이나 외부 정보를 추가해서는 안 됩니다. 분석은 기사의 단편적 정보를 연결하여 기술, 정책, 시장 관점의 구체적인 시사점을 도출하는 데 초점을 맞춥니다.

    # Persona
    - **정체성:** 20년 경력의 ICT 표준·정책 전문 애널리스트.
    - **전문성:** 기사 속 데이터와 인용문을 근거로, 기술적·정책적·시장적 인과관계를 분석하고 실질적인 파급효과를 예측하는 데 능숙함.
    - **핵심 원칙:** 철저한 '기사 기반(Article-Based)' 분석. 모든 분석과 전망은 기사의 특정 문장이나 수치에 기반하여 논리를 전개함.

    # Process (Step-by-Step)
    1.  **[1단계: 핵심 정보 추출]**
    - 기사에서 '누가, 언제, 어디서, 무엇을, 어떻게, 왜'에 해당하는 6하 원칙 기반의 핵심 사실(fact)을 모두 추출하여 목록화합니다.
    - 기사에 언급된 모든 구체적인 수치, 통계, 일정, 고유명사(인물, 기업, 기관, 기술명)를 정확히 식별합니다.
    - 주요 이해관계자들의 발언을 인용문 형태로 그대로 추출합니다.

    2.  **[2단계: 분석 및 보고서 작성]**
    - 아래 **[OUTPUT FORMAT]**에 정의된 구조에 따라 보고서를 작성합니다.
    - **[주요 내용 요약]** 파트: 1단계에서 추출한 객관적 사실만을 사용하여 기사의 핵심 내용을 재구성합니다. 어떠한 주관적 해석이나 외부 정보도 포함하지 않습니다.
    - **[시사점 및 전망]** 파트: **[주요 내용 요약]**에서 정리된 특정 사실이나 발언을 직접 인용하며, 그것이 왜 중요한지, 어떤 구체적인 영향을 미칠 것인지를 논리적으로 연결하여 분석합니다. "A라는 발언은 B라는 기술 표준 논의에 C와 같은 영향을 미칠 것"과 같이 명확하게 서술합니다.
    - 문장은 '~로 분석됨', '~로 판단됨', '~를 시사함' 등 **전문가적 판단을 가미한 서술형 문체**로 작성할 것.

    # CONSTRAINTS
    - **엄격한 근거 제시:** 모든 분석과 전망은 "기사에 따르면...", "OOO의 발언을 통해 볼 때..." 와 같이 명확한 근거를 제시해야 합니다.
    - **추론 금지:** 기사에 명시되지 않은 내용은 절대 언급하지 마십시오.
    - **구체성:** "큰 영향을 미칠 것"과 같은 추상적 표현 대신, "어떤 가치사슬(e.g., 칩셋, 단말, 플랫폼)에 어떤 변화를 유발할 것"처럼 구체적으로 서술하십시오.
    - **전문가적 문체:** '~로 판단됨', '~를 의미함', '~가 예상됨' 등 전문가의 분석적 어조를 일관되게 사용하십시오.
        
    # Input Data
    - 뉴스 제목: {news_item['title']}
    - 원문 링크: {news_item['link']}
    - 뉴스 본문:
    ---
    {news_item.get('content', '본문 내용을 가져올 수 없었습니다.')}
    ---

    # OUTPUT FORMAT

    ## **뉴스 심층 분석 보고서**

    ### **1. 주요 내용 요약**
    ㅇ [기사 본문을 기반으로 핵심 내용을 1-2개의 문장으로 요약. 누가, 무엇을 했는가에 초점을 맞추고, 문장을 'ㅇ'으로 시작하는 글머리 기호로 작성]
    ㅇ [기사에 나타난 사건의 배경과 원인을 객관적으로 서술. 문장을 'ㅇ'으로 시작하는 글머리 기호로 작성]
    ㅇ [기사에 언급된 핵심 수치, 일정, 데이터를 인용하고 그것이 의미하는 팩트를 설명. 문장을 'ㅇ'으로 시작하는 글머리 기호로 작성]
    ㅇ [주요 인물 또는 기관의 발언이나 공식 입장을 인용하여 정리. 문장을 'ㅇ'으로 시작하는 글머리 기호로 작성]

    ### **2. 시사점 및 전망**
    ㅇ [기사 내용이 ICT 기술, 표준, 정책, 산업에 미치는 영향과 전망을 'ㅇ'으로 시작하는 글머리 기호로 1~2문장으로 압축하여 서술]
    """
    
    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "당신은 ICT 표준 정책 분석 최고 전문가입니다. 제공된 기사 본문만을 근거로 '주요 내용 요약'과 '시사점 및 전망'을 작성합니다."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.3, max_tokens=1500, # 토큰 길이 상향
        )
        return response.choices[0].message.content
    except Exception as e:
        print(f"  (경고) AI 심층 분석 실패 ({news_item['title']}): {e}")
        return "AI 심층 분석에 실패했습니다."

# ==============================================================================
# --- 5. 구글 문서 생성 함수 (디자인 개선) ---
# ==============================================================================


def get_google_docs_service():
    """Google Docs와 Drive API 서비스를 인증하고 생성하는 함수"""
    creds = None
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
            creds = flow.run_local_server(port=0)
        with open('token.json', 'w') as token:
            token.write(creds.to_json())
            
    docs_service = build('docs', 'v1', credentials=creds)
    drive_service = build('drive', 'v3', credentials=creds)
    return docs_service, drive_service


def generate_google_doc_report(analyzed_data):
    try:
        docs_service, drive_service = get_google_docs_service()
    except FileNotFoundError:
        print("  (오류) 'credentials.json' 파일을 찾을 수 없습니다. 구글 인증 설정을 확인하세요.")
        return None, None
    except Exception as e:
        print(f"  (오류) 구글 서비스 연결에 실패했습니다: {e}")
        return None, None
        
    current_date = datetime.date.today().strftime('%Y년 %m월 %d일')
    document_title = f"전파·이동통신 동향 보고서 ({current_date})"

    try:
        # 1. 문서 생성
        document = docs_service.documents().create(body={'title': document_title}).execute()
        document_id = document.get('documentId')
        
        permission = {'type': 'anyone', 'role': 'reader'}
        drive_service.permissions().create(fileId=document_id, body=permission).execute()
        print("  > 문서 접근 권한을 공개로 설정했습니다.")
        
        document_url = f"https://docs.google.com/document/d/{document_id}/edit"
        print(f"  > 새 문서가 생성되었습니다: {document_url}")

        # 2. 스타일링된 내용 추가
        requests_list = []
        index = 1

        # --- 문서 제목 스타일링 ---
        title_text = f"{document_title}\n"
        requests_list.append({'insertText': {'location': {'index': index}, 'text': title_text}})
        requests_list.append({'updateParagraphStyle': {'range': {'startIndex': index, 'endIndex': index + len(title_text)}, 'paragraphStyle': {'alignment': 'CENTER'}, 'fields': 'alignment'}})
        requests_list.append({'updateTextStyle': {'range': {'startIndex': index, 'endIndex': index + len(title_text) - 1}, 'textStyle': {'fontSize': {'magnitude': 18, 'unit': 'PT'}, 'bold': True}, 'fields': 'fontSize,bold'}})
        index += len(title_text)
        
        # --- AI 분석 고지 문구 ---
        disclaimer_text = "※ 본 보고서의 내용은 AI가 생성한 분석으로, 개인적인 의견을 포함하지 않습니다.\n\n"
        requests_list.append({'insertText': {'location': {'index': index}, 'text': disclaimer_text}})
        requests_list.append({'updateParagraphStyle': {'range': {'startIndex': index, 'endIndex': index + len(disclaimer_text)}, 'paragraphStyle': {'alignment': 'CENTER'}, 'fields': 'alignment'}})
        requests_list.append({'updateTextStyle': {'range': {'startIndex': index, 'endIndex': index + len(disclaimer_text) - 2}, 'textStyle': {'fontSize': {'magnitude': 9, 'unit': 'PT'}, 'italic': True, 'foregroundColor': {'color': {'rgbColor': {'red': 0.5, 'green': 0.5, 'blue': 0.5}}}}, 'fields': 'fontSize,italic,foregroundColor'}})
        index += len(disclaimer_text)


        # --- 각 뉴스 아이템 스타일링 ---
        for i, data in enumerate(analyzed_data):
            # 뉴스 제목
            news_title = f"[{i+1}] {data['title']}\n"
            requests_list.append({'insertText': {'location': {'index': index}, 'text': news_title}})
            requests_list.append({'updateTextStyle': {'range': {'startIndex': index, 'endIndex': index + len(news_title)}, 'textStyle': {'fontSize': {'magnitude': 14, 'unit': 'PT'}, 'bold': True}, 'fields': 'fontSize,bold'}})
            index += len(news_title)
            
            # 메타데이터 (출처, 발행일, 링크)
            meta_text = f"출처: {data['source']} | 발행일: {data['published']}\n"
            requests_list.append({'insertText': {'location': {'index': index}, 'text': meta_text}})
            requests_list.append({'updateTextStyle': {'range': {'startIndex': index, 'endIndex': index + len(meta_text)}, 'textStyle': {'fontSize': {'magnitude': 9, 'unit': 'PT'}, 'foregroundColor': {'color': {'rgbColor': {'red': 0.5, 'green': 0.5, 'blue': 0.5}}}}, 'fields': 'fontSize,foregroundColor'}})
            index += len(meta_text)
            
            link_text = f"원본 링크: {data['link']}\n\n"
            requests_list.append({'insertText': {'location': {'index': index}, 'text': link_text}})
            requests_list.append({'updateTextStyle': {'range': {'startIndex': index, 'endIndex': index + len(link_text)}, 'textStyle': {'fontSize': {'magnitude': 9, 'unit': 'PT'}, 'link': {'url': data['link']}}, 'fields': 'fontSize,link'}})
            index += len(link_text)

            # 분석 내용 파싱 (정규식 수정)
            analysis_text = data.get('analysis_result', '')
            
            # 보고서 전체를 파싱
            report_match = re.search(r'## \*\*뉴스 심층 분석 보고서\*\*(.*)', analysis_text, re.DOTALL)
            if report_match:
                report_content = report_match.group(1).strip()
            else:
                report_content = analysis_text # 매치 안되면 그냥 전체 사용
            
            # 섹션 제목과 내용을 분리하여 스타일링
            sections = re.split(r'### \*\*(.*?)\*\*', report_content)
            
            # sections[0]은 보통 빈 문자열
            for k in range(1, len(sections), 2):
                section_title = sections[k].strip() + "\n"
                section_body = sections[k+1].strip() + "\n\n"

                # 섹션 타이틀
                requests_list.append({'insertText': {'location': {'index': index}, 'text': section_title}})
                requests_list.append({'updateTextStyle': {'range': {'startIndex': index, 'endIndex': index + len(section_title)}, 'textStyle': {'bold': True}, 'fields': 'bold'}})
                
                # 배경색
                if "주요 내용" in section_title:
                    bg_color = {'red': 0.91, 'green': 0.95, 'blue': 1.0}
                elif "시사점" in section_title:
                    bg_color = {'red': 1.0, 'green': 0.96, 'blue': 0.9}
                else:
                    bg_color = None

                if bg_color:
                    requests_list.append({'updateParagraphStyle': {'range': {'startIndex': index, 'endIndex': index + len(section_title)}, 'paragraphStyle': {'shading': {'backgroundColor': {'color': {'rgbColor': bg_color}}}}, 'fields': 'shading'}})
                index += len(section_title)
                
                # 섹션 본문
                requests_list.append({'insertText': {'location': {'index': index}, 'text': section_body}})
                index += len(section_body)


        # 3. 일괄 업데이트 실행
        docs_service.documents().batchUpdate(documentId=document_id, body={'requests': requests_list}).execute()
        
        return document_url, document_title
    except Exception as e:
        print(f"  (오류) 구글 문서 생성/스타일링 실패: {e}")
        return None, None


# ==============================================================================
# --- 6. Gmail 전송 함수 (템플릿 및 파싱 로직 수정) ---
# ==============================================================================
def send_gmail_report(report_title, analyzed_data, doc_url, other_news):
    """분석 리포트를 새로운 형식의 이메일로 전송하는 함수"""
    # 1. 심층 분석된 뉴스 HTML 생성
    news_items_html = ""
    for i, data in enumerate(analyzed_data):
        analysis_text = data.get('analysis_result', '')
        main_content = "주요내용 정보를 찾을 수 없습니다."
        implications = "시사점 정보를 찾을 수 없습니다."

        # 🔧 수정된 파싱 로직 - 다양한 형식을 처리할 수 있도록 개선
        try:
            print(f"  [디버그] 분석 텍스트 일부: {analysis_text[:200]}...")
            
            # 패턴 1: ### **1. 주요 내용 요약** 형식
            main_pattern1 = re.search(r'### \*\*1\. 주요 내용 요약\*\*(.*?)### \*\*2\. 시사점 및 전망\*\*', analysis_text, re.DOTALL)
            if main_pattern1:
                main_content = main_pattern1.group(1).strip()
                print(f"  [디버그] 패턴1로 주요내용 추출 성공")
            else:
                # 패턴 2: **1. 주요 내용 요약** 형식 (### 없이)
                main_pattern2 = re.search(r'\*\*1\. 주요 내용 요약\*\*(.*?)\*\*2\. 시사점 및 전망\*\*', analysis_text, re.DOTALL)
                if main_pattern2:
                    main_content = main_pattern2.group(1).strip()
                    print(f"  [디버그] 패턴2로 주요내용 추출 성공")
                else:
                    # 패턴 3: 1. 주요 내용 요약 형식 (별표 없이)
                    main_pattern3 = re.search(r'1\. 주요 내용 요약(.*?)2\. 시사점 및 전망', analysis_text, re.DOTALL)
                    if main_pattern3:
                        main_content = main_pattern3.group(1).strip()
                        print(f"  [디버그] 패턴3으로 주요내용 추출 성공")
                    else:
                        print(f"  [디버그] 주요내용 패턴 매칭 실패")

            # 시사점 파싱도 동일하게 다양한 패턴 지원
            # 패턴 1: ### **2. 시사점 및 전망** 형식
            impl_pattern1 = re.search(r'### \*\*2\. 시사점 및 전망\*\*(.*)', analysis_text, re.DOTALL)
            if impl_pattern1:
                implications = impl_pattern1.group(1).strip()
                print(f"  [디버그] 패턴1로 시사점 추출 성공")
            else:
                # 패턴 2: **2. 시사점 및 전망** 형식
                impl_pattern2 = re.search(r'\*\*2\. 시사점 및 전망\*\*(.*)', analysis_text, re.DOTALL)
                if impl_pattern2:
                    implications = impl_pattern2.group(1).strip()
                    print(f"  [디버그] 패턴2로 시사점 추출 성공")
                else:
                    # 패턴 3: 2. 시사점 및 전망 형식
                    impl_pattern3 = re.search(r'2\. 시사점 및 전망(.*)', analysis_text, re.DOTALL)
                    if impl_pattern3:
                        implications = impl_pattern3.group(1).strip()
                        print(f"  [디버그] 패턴3으로 시사점 추출 성공")
                    else:
                        print(f"  [디버그] 시사점 패턴 매칭 실패")
            
            # 마크다운 문법 정리 (*, ** 제거)
            main_content = re.sub(r'\*+', '', main_content).strip()
            implications = re.sub(r'\*+', '', implications).strip()
            
        except Exception as e:
            print(f"  (경고) AI 분석 결과 파싱 중 오류 발생: {e}")

        news_items_html += f"""
        <div class="news-item">
            <div class="news-header">
                <h3 class="news-title">{data['title']}</h3>
                <div class="news-meta">
                    <span><strong>출처:</strong> {data['source']}</span>
                    <span><strong>발행일:</strong> {data['published']}</span>
                    <span><a href="{data['link']}" target="_blank">원문 기사 보기 &rarr;</a></span>
                </div>
            </div>
            <div class="analysis-container">
                <div class="analysis-section summary">
                    <div class="analysis-title"><span class="icon">📝</span><strong>주요 내용</strong></div>
                    <p class="analysis-text">{main_content.replace('ㅇ', '&#8226;').replace('\n', '<br>')}</p>
                </div>
                <div class="analysis-section implications">
                    <div class="analysis-title"><span class="icon">💡</span><strong>시사점 및 전망</strong></div>
                    <p class="analysis-text">{implications.replace('ㅇ', '&#8226;').replace('\n', '<br>')}</p>
                </div>
            </div>
        </div>"""

    # 2. 기타 뉴스 HTML 생성 (변경 없음)
    other_news_html = ""
    if other_news:
        other_news_html += """
        <div class="other-news-section">
            <h2>기타 수집된 뉴스</h2>
            <ul class="other-news-list">
        """
        for item in other_news:
            other_news_html += f'<li><a href="{item["link"]}" target="_blank" class="other-news-link"><span class="other-news-title">{item["title"]}</span><span class="other-news-source">({item["source"]})</span></a></li>'
        
        other_news_html += "</ul></div>"


    # 3. 전체 이메일 본문 조합 (템플릿 수정)
    html_body = f"""
    <!DOCTYPE html>
    <html lang="ko"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>ICT 주요기술 동향 리포트</title>
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@300;400;500;700&display=swap');
        body {{ margin: 0; padding: 0; background-color: #f4f7fa; font-family: 'Noto Sans KR', sans-serif; }}
        .email-container {{ max-width: 700px; margin: 20px auto; background-color: #ffffff; border-radius: 12px; overflow: hidden; box-shadow: 0 10px 40px rgba(0, 0, 0, 0.05); border: 1px solid #e9e9e9; }}
        .header {{ background: linear-gradient(135deg, #1D2B4A 0%, #2C3E6A 100%); color: #ffffff; padding: 40px; text-align: center; }}
        .header h1 {{ margin: 0; font-size: 28px; font-weight: 700; }} .header p {{ margin: 8px 0 0; font-size: 16px; font-weight: 300; opacity: 0.8; }}
        .disclaimer {{ font-size: 12px; opacity: 0.7; font-style: italic; margin-top: 15px;}}
        .main-content {{ padding: 40px; }}
        .report-intro {{ text-align: center; padding-bottom: 30px; border-bottom: 1px solid #eaeaea; margin-bottom: 30px; }}
        .button {{ display: inline-block; background: linear-gradient(135deg, #4A6DFF 0%, #6284FF 100%); color: #ffffff; padding: 14px 28px; border-radius: 50px; text-decoration: none; font-weight: 500; font-size: 15px; transition: all 0.3s ease; box-shadow: 0 4px 15px rgba(74, 109, 255, 0.3); }}
        .button:hover {{ transform: translateY(-2px); box-shadow: 0 8px 25px rgba(74, 109, 255, 0.4); }}
        .news-item {{ border: 1px solid #e9e9e9; border-radius: 12px; margin-bottom: 25px; overflow: hidden; transition: all 0.3s ease; }}
        .news-item:hover {{ transform: translateY(-2px); box-shadow: 0 8px 25px rgba(0, 0, 0, 0.07); }}
        .news-header {{ padding: 25px; }} .news-title {{ font-size: 20px; font-weight: 700; color: #1D2B4A; margin: 0 0 15px; }}
        .news-meta {{ font-size: 13px; color: #777; }} .news-meta a {{ color: #4A6DFF; text-decoration: none; font-weight: 500; }} .news-meta span {{ margin-right: 15px; }}
        .analysis-container {{ padding: 0 25px 25px 25px; border-top: 1px solid #e9e9e9; background-color: #f8f9fc; }}
        .analysis-section {{ padding: 20px; border-radius: 8px; margin-top: 15px; }}
        .analysis-section.summary {{ background-color: #e9f3ff; border-left: 4px solid #4A6DFF; }}
        .analysis-section.implications {{ background-color: #fff6e9; border-left: 4px solid #ff9f43; }}
        .analysis-title {{ display: flex; align-items: center; font-size: 16px; font-weight: 700; color: #1D2B4A; margin-bottom: 10px; }}
        .analysis-title .icon {{ font-size: 20px; margin-right: 10px; }}
        .analysis-text {{ font-size: 14px; line-height: 1.7; color: #333; margin: 0; }}
        .other-news-section {{ margin-top: 40px; padding-top: 30px; border-top: 1px solid #eaeaea; }}
        .other-news-section h2 {{ font-size: 20px; font-weight: 700; color: #1D2B4A; margin-bottom: 20px; text-align: center; }}
        .other-news-list {{ list-style-type: none; padding: 0; margin: 0; }}
        .other-news-list li {{ border-radius: 8px; margin-bottom: 5px; border-left: 3px solid #ccc; background-color: #f8f9fc; transition: background-color 0.2s ease; }}
        .other-news-list li:hover {{ background-color: #f1f3f8; }}
        .other-news-link {{ display: flex; justify-content: space-between; align-items: center; padding: 12px 15px; text-decoration: none; color: inherit; }}
        .other-news-title {{ color: #333; font-size: 14px; }}
        .other-news-source {{ color: #888; font-size: 12px; white-space: nowrap; margin-left: 15px; }}
        .footer {{ text-align: center; padding: 30px; background-color: #f4f7fa; font-size: 13px; color: #999; }}
    </style></head>
    <body><div class="email-container">
        <div class="header"><h1>{report_title}</h1><p class="disclaimer">※ 본 보고서의 내용은 IRONAGE AI가 생성한 분석으로, 개인적인 의견을 포함하지 않습니다.</p></div>
        <div class="main-content">
            <div class="report-intro">
                <a href="{doc_url}" class="button" target="_blank">📄 전체 보고서 보기</a>
            </div>
            {news_items_html}
            {other_news_html}
        </div>
        <div class="footer"><p>본 리포트는 AI 기술을 활용해 자동 생성된 분석 보고서입니다.</p><p>Powered by Advanced IRONAGE AI Analytics</p></div>
    </div></body></html>"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = report_title
    msg["From"] = SENDER_EMAIL
    msg["To"] = ", ".join(RECEIVER_EMAIL)
    msg["Date"] = formatdate(localtime=True)
    msg.attach(MIMEText(html_body, 'html', 'utf-8'))
    
    try:
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(SENDER_EMAIL, GMAIL_PASSWORD)
        server.sendmail(SENDER_EMAIL, RECEIVER_EMAIL, msg.as_string())
        server.quit()
        print(f"  > ✅ 이메일이 {', '.join(RECEIVER_EMAIL)} 주소로 성공적으로 발송되었습니다.")
    except Exception as e:
        print(f"  (오류) 이메일 발송에 실패했습니다: {e}")


# ==============================================================================
# --- 7. 디버깅 함수 추가 ---
# ==============================================================================
def debug_analysis_parsing(analyzed_data):
    """AI 분석 결과 파싱을 디버깅하는 함수"""
    print("\n🔍 [디버그] AI 분석 결과 파싱 테스트...")
    
    for i, data in enumerate(analyzed_data[:3]):  # 처음 3개만 테스트
        print(f"\n--- 뉴스 {i+1}: {data['title'][:50]}... ---")
        analysis_text = data.get('analysis_result', '')
        
        if not analysis_text or analysis_text == "AI 심층 분석에 실패했습니다.":
            print("❌ AI 분석 결과가 없음")
            continue
            
        print(f"📄 AI 응답 전체 길이: {len(analysis_text)} 문자")
        print(f"📄 AI 응답 첫 300자:")
        print(analysis_text[:300])
        print("...")
        
        # 주요 패턴들을 테스트
        patterns_to_test = [
            (r'### \*\*1\. 주요 내용 요약\*\*', "패턴1: ### **1. 주요 내용 요약**"),
            (r'\*\*1\. 주요 내용 요약\*\*', "패턴2: **1. 주요 내용 요약**"),
            (r'1\. 주요 내용 요약', "패턴3: 1. 주요 내용 요약"),
            (r'## \*\*뉴스 심층 분석 보고서\*\*', "패턴4: ## **뉴스 심층 분석 보고서**"),
        ]
        
        print("🔍 패턴 매칭 테스트:")
        for pattern, description in patterns_to_test:
            match = re.search(pattern, analysis_text)
            print(f"  {description}: {'✅ 발견' if match else '❌ 없음'}")


# ==============================================================================
# --- 8. 메인 실행 부분 (디버깅 추가) ---
# ==============================================================================
if __name__ == "__main__":
    print("==============================================")
    print("AI 뉴스 리포트 자동 생성 스크립트를 시작합니다.")
    print("==============================================")
    
    print("\n[작업 시작] 뉴스 수집 및 중복 제거를 시작합니다...")
    unique_news_items = get_news_data()
    print(f"  > 총 {len(unique_news_items)}개의 고유한 뉴스를 수집했습니다.")

    # AI를 사용해 정책 입안자에게 중요한 뉴스를 필터링합니다.
    news_to_analyze = filter_news_by_ai(unique_news_items)
    print(f"  > AI가 선별한 {len(news_to_analyze)}개의 핵심 뉴스를 심층 분석합니다.")
    
    # 선별되지 않은 나머지 뉴스를 찾습니다.
    analyzed_links = {item['link'] for item in news_to_analyze}
    other_news = [item for item in unique_news_items if item['link'] not in analyzed_links]


    analyzed_results = []
    if news_to_analyze:
        print("\n[🚀 작업 중] 선택된 뉴스에 대한 심층 분석을 시작합니다...")
        for i, item in enumerate(news_to_analyze):
            print(f"  ({i+1}/{len(news_to_analyze)}) 분석 중: {item['title'][:40]}...")
            
            # 💡💡💡 --- [수정] AI 분석 전, 뉴스 본문 수집 단계 추가 --- 💡💡💡
            print(f"      -> 본문 수집 중...")
            item['content'] = get_article_content(item['link'])
            if "실패" in item['content'] or "추출하지 못했습니다" in item['content']:
                 print(f"      (경고) {item['content']}")

            analysis = analyze_news_with_ai(item)
            item['analysis_result'] = analysis
            analyzed_results.append(item)

    # 🔍 디버깅 함수 실행
    if analyzed_results:
        debug_analysis_parsing(analyzed_results)
        
        print("\n[🚀 작업 중] 구글 문서 보고서를 생성하고 있습니다...")
        generated_doc_url, report_title = generate_google_doc_report(analyzed_results)

        if report_title:
            print("\n[🚀 작업 중] 생성된 리포트를 이메일로 발송합니다...")
            # 'other_news' 리스트를 함께 전달합니다.
            send_gmail_report(report_title, analyzed_results, generated_doc_url, other_news)

    print("\n==============================================")
    print("🎉 모든 작업이 완료되었습니다!")
    print("==============================================")
