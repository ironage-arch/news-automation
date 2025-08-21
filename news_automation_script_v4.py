import feedparser
import requests
import json
import re
import openai
import os
import os.path
import datetime
import smtplib
import getpass
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate
from email.header import Header
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
import difflib

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
# --- 헬퍼 함수: URL 최종 목적지 추적 (고도화) ---
# ==============================================================================
def get_final_url(url):
    """리디렉션을 추적하여 최종 URL을 찾아내는 고도화된 함수"""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        response = requests.head(url, headers=headers, allow_redirects=True, timeout=10)
        response.raise_for_status()
        return response.url
    except requests.RequestException:
        try:
            response = requests.get(url, headers=headers, allow_redirects=True, timeout=10)
            response.raise_for_status()
            return response.url
        except requests.RequestException as e:
            return url

# ==============================================================================
# --- 2. 뉴스 수집 함수 (중복 제거 로직 개선) ---
# ==============================================================================
def get_news_data():
    """여러 RSS 피드와 키워드에서 뉴스를 수집하고 중복을 정확하게 제거하는 함수"""
    news_list = []
    
    print("\n- Google Alerts에서 뉴스를 수집합니다...")
    for url in GOOGLE_ALERTS_RSS_URLS:
        if not url.strip(): continue
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries:
                raw_link = entry.link.split("&url=")[1] if "&url=" in entry.link else entry.link
                final_link = get_final_url(raw_link)
                published_date = datetime.datetime(*entry.published_parsed[:6]).strftime('%Y-%m-%d') if hasattr(entry, 'published_parsed') else "날짜 정보 없음"
                news_list.append({"title": entry.title, "link": final_link, "published": published_date, "source": "Google Alerts"})
        except Exception as e:
            print(f"  (경고) 구글 알리미 RSS 불러오기 실패 ({url}): {e}")

    print("- Naver News에서 뉴스를 수집합니다...")
    for query in NAVER_QUERIES:
        if not query.strip(): continue
        try:
            naver_url = "https://openapi.naver.com/v1/search/news.json"
            headers = {"X-Naver-Client-Id": NAVER_CLIENT_ID, "X-Naver-Client-Secret": NAVER_CLIENT_SECRET}
            params = {"query": query, "display": 10, "sort": "date"}
            response = requests.get(naver_url, headers=headers, params=params)
            response.raise_for_status()
            data = response.json()
            for item in data.get("items", []):
                clean_title = re.sub('<[^>]*>', '', item["title"])
                published_date = datetime.datetime.strptime(item['pubDate'], '%a, %d %b %Y %H:%M:%S +0900').strftime('%Y-%m-%d')
                raw_link = item.get("originallink", item["link"])
                news_list.append({"title": clean_title, "link": raw_link, "published": published_date, "source": "Naver News"})
        except Exception as e:
            print(f"  (경고) 네이버 뉴스 API 불러오기 실패 ({query}): {e}")

    news_list.sort(key=lambda x: x['published'], reverse=True)
    unique_news_items = []
    seen_links = set()
    seen_titles = []

    for item in news_list:
        normalized_link = re.sub(r'^https?:\/\/(www\.)?', '', item['link']).rstrip('/')
        if normalized_link in seen_links:
            continue
        
        is_similar = False
        for seen_title in seen_titles:
            similarity = difflib.SequenceMatcher(None, item['title'], seen_title).ratio()
            if similarity > 0.85:
                is_similar = True
                break
        
        if not is_similar:
            unique_news_items.append(item)
            seen_links.add(normalized_link)
            seen_titles.append(item['title'])
            
    return unique_news_items

# ==============================================================================
# --- 3. AI 뉴스 선별 함수 ---
# ==============================================================================
def filter_news_by_ai(news_items):
    """AI를 사용해 정책 입안자에게 가장 관련성 높은 뉴스를 선별하는 함수"""
    print("\n[🚀 작업 중] AI가 정책 입안자를 위해 뉴스를 선별하고 있습니다...")
    if not OPENAI_API_KEY or not OPENAI_API_KEY.startswith("sk-"):
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
    - **해외 주요국 정책/규제**: 미국(FCC), 유럽(ETSI), 영국(Ofcom) 등 해외 주요 ICT 규제기관 및 정책 당국의 법안, 규제, 정책 변화, 글로벌 ICT 거버넌스 및 규제 프레임워크 변화
    - **국제 표준화 동향**: 3GPP, ITU, IEEE, ISO/IEC JTC 1 등 국제 표준화 기구의 의사결정 결과, 차기 의제, 주요 합의 사항, 차세대 기술(6G, AI, 위성통신, 자율주행, 양 등) 표준화 방향성
    - **국내 정부 계획 및 발표**: 과기정통부, 방통위, 산업자원 등 국내 정부 부처의 정책 발표, 법·제도 신설·개정, 국가 R&D 전략, 디지털 규제, 공공안전통신, 주파수 정책 등 핵심 정책
    - **산업계 핵심 동향**: ICT 산업 및 시장 판도에 큰 영향을 미치는 국내외 기업의 기술 개발 및 사업 전략
    - **정책 비판 및 대안**: 현재 정책의 문제점을 지적하거나 새로운 대안을 제시하는 기사
    - **TTA 보도자료**: TTA 공식 보도자료, 표준 제정·개정 발표, 손승현 회장 등 주요 인사의 발언, 인터뷰, 기고


    [뉴스 목록]
    {formatted_news_list}

    [요청]
    위 절차와 기준에 따라 최종적으로 선별된 뉴스의 번호 20개만 쉼표(,)로 구분하여 응답해 주십시오.
    예시: 3, 8, 12, 15, 21, 23, 25, 30, 31, 33, 40, 41, 42, 45, 50, 51, 52, 53, 54, 55
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
        if not filtered_news: raise ValueError("AI가 유효한 인덱스를 반환하지 않았습니다.")
        return filtered_news
    except Exception as e:
        print(f"  (경고) AI 뉴스 선별 실패: {e}. 최신 뉴스 20개로 대체합니다.")
        return news_items[:20]

# ==============================================================================
# --- 4. AI 심층 분석 함수 (보고서 형식 구체화) ---
# ==============================================================================
def analyze_news_with_ai(news_item):
    """AI에게 뉴스를 보내 구체화된 전문가 보고서 형식으로 심층 분석을 요청하는 함수"""
    if not OPENAI_API_KEY or not OPENAI_API_KEY.startswith("sk-"):
        return "OpenAI API 키가 설정되지 않아 분석을 건너뜁니다."
        
    client = openai.OpenAI(api_key=OPENAI_API_KEY)
    prompt = f"""
    당신은 20년 경력의 ICT 표준 및 정책 분야 최고 전문가입니다.
    아래 뉴스 기사를 분석하여, 다음 4가지 항목으로 구성된 전문가 보고서를 작성해 주십시오.
    모든 내용은 중학생도 이해할 수 있도록 명확하고 쉬운 언어를 사용해야 합니다.

    [뉴스 정보]
    - 뉴스 제목: {news_item['title']}
    - 원문 링크: {news_item['link']}

    [보고서 작성 형식]
    1. **핵심 요약:** (기사 전체 내용을 단 한 문장으로 압축하여 요약)
    2. **주요 내용:** (기사의 핵심 사실과 정보를 3개 항목으로 나누어箇条書き(bullet point)로 정리)
       - 
       - 
       - 
    3. **정책적 시사점:** (이 뉴스가 ICT 표준, 규제, 정부 정책에 미치는 영향이나 의미를 분석)
    4. **기대 효과 및 전망:** (향후 기술 발전, 시장 변화, 사회적 파급 효과 등을 예측)
    """
    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "당신은 ICT 표준 정책 분석 최고 전문가입니다. 모든 답변은 지정된 4가지 보고서 형식에 맞춰, 쉽고 명확한 언어로 작성해 주세요."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.5, max_tokens=1024,
        )
        return response.choices[0].message.content
    except Exception as e:
        print(f"  (경고) AI 심층 분석 실패 ({news_item['title']}): {e}")
        return "AI 심층 분석에 실패했습니다."

# ==============================================================================
# --- 5. 구글 문서 생성 함수 (API 오류 수정) ---
# ==============================================================================
def get_google_services():
    """Google Docs와 Drive API 서비스를 인증하고 생성하는 함수"""
    creds = None
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as e:
                print(f" (정보) 토큰 갱신 실패: {e}. 'token.json'을 삭제하고 다시 인증을 시도합니다.")
                if os.path.exists('token.json'):
                    os.remove('token.json')
                creds = None
    
    if not creds:
        flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
        creds = flow.run_local_server(port=0)
        with open('token.json', 'w') as token:
            token.write(creds.to_json())
            
    docs_service = build('docs', 'v1', credentials=creds)
    drive_service = build('drive', 'v3', credentials=creds)
    return docs_service, drive_service

def generate_google_doc_report(analyzed_data):
    try:
        docs_service, drive_service = get_google_services()
    except FileNotFoundError:
        print("  (오류) 'credentials.json' 파일을 찾을 수 없습니다. 구글 인증 설정을 확인하세요.")
        return None, None
    except Exception as e:
        print(f"  (오류) 구글 서비스 연결에 실패했습니다: {e}")
        return None, None
        
    current_date = datetime.date.today().strftime('%Y년 %m월 %d일')
    document_title = f"ICT 주요 기술 동향 보고서 ({current_date})"
    
    try:
        document = docs_service.documents().create(body={'title': document_title}).execute()
        document_id = document.get('documentId')
        
        permission = {'type': 'anyone', 'role': 'reader'}
        drive_service.permissions().create(fileId=document_id, body=permission).execute()
        print("  > 문서 접근 권한을 공개로 설정했습니다.")

        document_url = f"https://docs.google.com/document/d/{document_id}/edit"
        print(f"  > 새 문서가 생성되었습니다: {document_url}")

        requests = []
        index = 1
        
        requests.append({'insertText': {'location': {'index': index}, 'text': document_title + '\n'}})
        requests.append({'updateParagraphStyle': {'range': {'startIndex': 1, 'endIndex': len(document_title)+1}, 'paragraphStyle': {'namedStyleType': 'TITLE', 'alignment': 'CENTER', 'spaceBelow': {'magnitude': 12, 'unit': 'PT'}}, 'fields': '*'}})
        index += len(document_title) + 1

        disclaimer = "본 보고서는 AI가 주요 뉴스를 분석하여 작성했으며, 개인적인 의견을 포함하지 않습니다.\n\n"
        requests.append({'insertText': {'location': {'index': index}, 'text': disclaimer}})
        requests.append({'updateParagraphStyle': {'range': {'startIndex': index, 'endIndex': index + len(disclaimer)}, 'paragraphStyle': {'alignment': 'CENTER'}, 'fields': 'alignment'}})
        requests.append({'updateTextStyle': {'range': {'startIndex': index, 'endIndex': index + len(disclaimer)}, 'textStyle': {'fontSize': {'magnitude': 9, 'unit': 'PT'}, 'foregroundColor': {'color': {'rgbColor': {'red': 0.4, 'green': 0.4, 'blue': 0.4}}}}, 'fields': 'fontSize,foregroundColor'}})
        index += len(disclaimer)

        for i, data in enumerate(analyzed_data):
            news_title = f"{i+1}. {data['title']}\n"
            requests.append({'insertText': {'location': {'index': index}, 'text': news_title}})
            
            # --- 💡 오류 수정 지점 ---
            # borderBottom 객체에 'dashStyle': 'SOLID'를 추가합니다.
            border_bottom_style = {
                'width': {'magnitude': 1, 'unit': 'PT'},
                'padding': {'magnitude': 2, 'unit': 'PT'},
                'color': {'color': {'rgbColor': {'red': 0.8, 'green': 0.8, 'blue': 0.8}}},
                'dashStyle': 'SOLID' 
            }
            
            requests.append({'updateParagraphStyle': {
                'range': {'startIndex': index, 'endIndex': index + len(news_title)},
                'paragraphStyle': {
                    'namedStyleType': 'HEADING_1',
                    'spaceAbove': {'magnitude': 18, 'unit': 'PT'},
                    'spaceBelow': {'magnitude': 4, 'unit': 'PT'},
                    'borderBottom': border_bottom_style
                },
                'fields': 'namedStyleType,spaceAbove,spaceBelow,borderBottom'
            }})
            index += len(news_title)
            
            meta_text = f"출처: {data['source']} | 발행일: {data['published']}\n"
            requests.append({'insertText': {'location': {'index': index}, 'text': meta_text}})
            requests.append({'updateParagraphStyle': {'range': {'startIndex': index, 'endIndex': index + len(meta_text)},'paragraphStyle': {'spaceBelow': {'magnitude': 6, 'unit': 'PT'}},'fields': 'spaceBelow'}})
            requests.append({'updateTextStyle': {'range': {'startIndex': index, 'endIndex': index + len(meta_text)}, 'textStyle': {'fontSize': {'magnitude': 9, 'unit': 'PT'}, 'foregroundColor': {'color': {'rgbColor': {'red': 0.5, 'green': 0.5, 'blue': 0.5}}}}, 'fields': 'fontSize,foregroundColor'}})
            index += len(meta_text)

            link_text = f"원본 링크 바로가기\n\n"
            requests.append({'insertText': {'location': {'index': index}, 'text': link_text}})
            requests.append({'updateTextStyle': {'range': {'startIndex': index, 'endIndex': index + len(link_text)}, 'textStyle': {'fontSize': {'magnitude': 9, 'unit': 'PT'}, 'link': {'url': data['link']}}, 'fields': 'fontSize,link'}})
            index += len(link_text)
            
            analysis_text = data.get('analysis_result', '')
            
            sections = {
                "핵심 요약": re.search(r'\*\*(핵심 요약):\*\*\s*(.*?)(?=\s*\*\*|\Z)', analysis_text, re.DOTALL),
                "주요 내용": re.search(r'\*\*(주요 내용):\*\*\s*(.*?)(?=\s*\*\*|\Z)', analysis_text, re.DOTALL),
                "정책적 시사점": re.search(r'\*\*(정책적 시사점):\*\*\s*(.*?)(?=\s*\*\*|\Z)', analysis_text, re.DOTALL),
                "기대 효과 및 전망": re.search(r'\*\*(기대 효과 및 전망):\*\*\s*(.*?)(?=\s*\*\*|\Z)', analysis_text, re.DOTALL)
            }
            
            for title, match in sections.items():
                if match:
                    section_title = f"{title}\n"
                    requests.append({'insertText': {'location': {'index': index}, 'text': section_title}})
                    requests.append({'updateParagraphStyle': {'range': {'startIndex': index, 'endIndex': index + len(section_title)}, 'paragraphStyle': {'namedStyleType': 'HEADING_3', 'spaceAbove': {'magnitude': 12, 'unit': 'PT'}, 'spaceBelow': {'magnitude': 3, 'unit': 'PT'}}, 'fields': '*'}})
                    index += len(section_title)
                    
                    content_body = match.group(2).strip().replace("   -", "-").replace("  -", "-") + "\n\n"
                    requests.append({'insertText': {'location': {'index': index}, 'text': content_body}})
                    if "- " in content_body:
                         requests.append({'createParagraphBullets': {'range': {'startIndex': index, 'endIndex': index + len(content_body)}, 'bulletPreset': 'BULLET_DISC_CIRCLE_SQUARE'}})
                    index += len(content_body)

        docs_service.documents().batchUpdate(documentId=document_id, body={'requests': requests}).execute()
        return document_url, document_title
    except Exception as e:
        print(f"  (오류) 구글 문서 생성/스타일링 실패: {e}")
        return None, None

# ==============================================================================
# --- 6. Gmail 전송 함수 (기존과 동일) ---
# ==============================================================================
def send_gmail_report(report_title, analyzed_data, doc_url, other_news):
    # 이 함수는 수정 없이 그대로 사용 가능합니다.
    # ... (기존 send_gmail_report 함수 코드)
    pass

# ==============================================================================
# --- 7. 메인 실행 부분 (기존과 동일) ---
# ==============================================================================
if __name__ == "__main__":
    print("==============================================")
    print("🤖 AI 뉴스 리포트 자동 생성 스크립트를 시작합니다.")
    print("==============================================")
    
    print("\n[🚀 작업 시작] 뉴스 수집 및 중복 제거를 시작합니다...")
    unique_news_items = get_news_data()
    print(f"  > 총 {len(unique_news_items)}개의 고유한 뉴스를 수집했습니다.")
    
    news_to_analyze = filter_news_by_ai(unique_news_items)
    print(f"  > AI가 선별한 {len(news_to_analyze)}개의 핵심 뉴스를 심층 분석합니다.")
    
    analyzed_links = {item['link'] for item in news_to_analyze}
    other_news = [item for item in unique_news_items if item['link'] not in analyzed_links]
    
    analyzed_results = []
    if news_to_analyze:
        print("\n[🚀 작업 중] 선택된 뉴스에 대한 심층 분석을 시작합니다...")
        for i, item in enumerate(news_to_analyze):
            print(f"  ({i+1}/{len(news_to_analyze)}) 분석 중: {item['title'][:40]}...")
            analysis = analyze_news_with_ai(item)
            item['analysis_result'] = analysis
            analyzed_results.append(item)
            
    if analyzed_results:
        print("\n[🚀 작업 중] 구글 문서 보고서를 생성하고 있습니다...")
        generated_doc_url, report_title = generate_google_doc_report(analyzed_results)
        
        if report_title:
            print("\n[🚀 작업 중] 생성된 리포트를 이메일로 발송합니다...")
            # send_gmail_report(report_title, analyzed_results, generated_doc_url, other_news)
            print("   (정보) 이메일 발송은 주석 처리되어 있습니다. 필요 시 주석을 해제하세요.")

    print("\n==============================================")
    print("🎉 모든 작업이 완료되었습니다!")
    print("==============================================")
