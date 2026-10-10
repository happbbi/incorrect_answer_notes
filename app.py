import io
import os
import re
import sqlite3
import fitz  # PyMuPDF
import streamlit as st
from PIL import Image
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

# ---------------------------------------------------------------------------
# 데이터베이스 초기화 및 연동 함수
# ---------------------------------------------------------------------------
DB_FILE = "study_notes.db"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    # 과목 테이블
    c.execute("""
        CREATE TABLE IF NOT EXISTS subjects (
            name TEXT PRIMARY KEY
        )
    """)
    # 문제 테이블
    c.execute("""
        CREATE TABLE IF NOT EXISTS problems (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subject TEXT,
            file_name TEXT,
            file_group TEXT,
            label TEXT,
            data BLOB,
            checked INTEGER,
            FOREIGN KEY (subject) REFERENCES subjects(name) ON DELETE CASCADE
        )
    """)
    conn.commit()
    
    # 기본 과목이 없으면 기본값 삽입
    c.execute("SELECT count(*) FROM subjects")
    if c.fetchone()[0] == 0:
        for subj in ["수학", "영어", "국어"]:
            c.execute("INSERT OR IGNORE INTO subjects (name) VALUES (?)", (subj,))
        conn.commit()
    conn.close()

def load_data_from_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    
    # 과목 목록 불러오기
    c.execute("SELECT name FROM subjects")
    subjects_list = [row[0] for row in c.fetchall()]
    
    # 문제 목록 불러오기
    c.execute("SELECT id, subject, file_name, file_group, label, data, checked FROM problems")
    rows = c.fetchall()
    
    uploaded_problems = {}
    for row in rows:
        pid, subj, fname, fgroup, label, data, checked = row
        if subj not in uploaded_problems:
            uploaded_problems[subj] = []
        uploaded_problems[subj].append({
            "id": pid,
            "file_name": fname,
            "file": fgroup,
            "label": label,
            "type": "image",
            "data": data,
            "checked": bool(checked)
        })
    conn.close()
    return subjects_list, uploaded_problems

# 앱 시작 시 DB 초기화 및 세션 상태 동기화
init_db()
if "subjects_list" not in st.session_state or "uploaded_problems" not in st.session_state:
    s_list, u_probs = load_data_from_db()
    st.session_state["subjects_list"] = s_list
    st.session_state["uploaded_problems"] = u_probs

# ---------------------------------------------------------------------------
# 분할 및 유틸 로직
# ---------------------------------------------------------------------------
NUM_RE = re.compile(r"^(\d{1,3})(?:[.)]|$)")

def setup_tessdata():
    if os.environ.get("TESSDATA_PREFIX"):
        return
    candidates = [
        r"C:\Program Files\Tesseract-OCR\tessdata",
        r"C:\Program Files (x86)\Tesseract-OCR\tessdata",
        "/opt/homebrew/share/tessdata",
        "/usr/local/share/tessdata",
        "/usr/share/tesseract-ocr/5/tessdata",
        "/usr/share/tesseract-ocr/4.00/tessdata",
        "/usr/share/tessdata",
    ]
    for p in candidates:
        if os.path.isdir(p):
            os.environ["TESSDATA_PREFIX"] = p
            return

setup_tessdata()

_OCR_FIX = str.maketrans({"l": "1", "I": "1", "|": "1", "O": "0", "o": "0", "S": "5", "B": "8"})
_OCR_NUMLIKE = re.compile(r"^[0-9lIO|oSB]{1,3}[.)]?$")

def fix_ocr_digits(tok):
    return tok.translate(_OCR_FIX) if _OCR_NUMLIKE.match(tok) else tok

def get_page_words(page, force_ocr=False):
    if not force_ocr:
        words = page.get_text("words")
        if len(words) >= 5:
            return words, False
    dpi = int(max(72, min(300, 2500 / (page.rect.width / 72))))
    tp = page.get_textpage_ocr(language="kor+eng", dpi=dpi, full=True)
    return page.get_text("words", textpage=tp), True

def pix_to_jpeg_bytes(pix):
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=92)
    return buf.getvalue()

def find_question_starts(col_words, x0, margin_pt, expected, used_ocr, max_skip=2, lenient=False):
    band = x0 + margin_pt
    merged = []
    for w in col_words:
        if merged:
            p = merged[-1]
            h = max(1.0, p[3] - p[1])
            if (p[0] < band and abs(w[1] - p[1]) < 0.5 * h and 0 <= w[0] - p[2] < 0.8 * h
                    and re.fullmatch(r"\d", p[4]) and re.fullmatch(r"\d[.)]?", w[4])):
                merged[-1] = (p[0], min(p[1], w[1]), w[2], max(p[3], w[3]), p[4] + w[4]) + tuple(p[5:])
                continue
        merged.append(w)

    cands = []
    for w in merged:
        tok = fix_ocr_digits(w[4]) if used_ocr else w[4]
        m = NUM_RE.match(tok)
        if m and w[0] < band:
            cands.append((int(m.group(1)), w))
    if not cands:
        return [], expected

    tol = max(4.0, margin_pt * 0.1)
    clusters = []
    for idx in sorted(range(len(cands)), key=lambda i: cands[i][1][0]):
        x = cands[idx][1][0]
        if clusters and x - clusters[-1][0] <= tol:
            clusters[-1][1].append(idx)
        else:
            clusters.append([x, [idx]])

    def simulate(indices, exp):
        accepted = 0
        for i in sorted(indices):
            n = cands[i][0]
            if exp <= n <= exp + max_skip:
                accepted += 1
                exp = n + 1
        return accepted

    sims = [simulate(c[1], expected) for c in clusters]
    chosen = next((k for k, s in enumerate(sims) if s >= 2), None)
    if chosen is None:
        chosen = max(range(len(clusters)), key=lambda k: sims[k])
    keep = set(clusters[chosen][1])

    seed = [cands[i][1] for i in sorted(keep)]
    if len(seed) >= 3:
        ys = [s[1] for s in seed]
        xs = [s[0] for s in seed]
        cnt = len(seed)
        my, mx = sum(ys) / cnt, sum(xs) / cnt
        var = sum((yy - my) ** 2 for yy in ys)
        slope = sum((yy - my) * (xx - mx) for yy, xx in zip(ys, xs)) / var if var else 0.0
        if abs(slope) < 0.1:
            icpt = mx - slope * my
            keep = {i for i, (_, cw) in enumerate(cands)
                    if abs(cw[0] - (slope * cw[1] + icpt)) <= tol}

    def next_num(idx):
        for j in range(idx + 1, len(cands)):
            if j in keep:
                return cands[j][0]
        return None

    results, last_y = [], None
    for idx, (n, w) in enumerate(cands):
        if idx not in keep:
            results.append((n, w, False, "번호 열이 아님"))
            continue
        h = max(1.0, w[3] - w[1])
        gap = None if last_y is None else w[1] - last_y
        if gap is not None and gap < 0.5 * h:
            continue
        nxt = next_num(idx)
        if n == expected:
            label, note = n, ""
        elif n < expected and nxt == expected and gap is not None and gap < 3 * h:
            continue
        elif nxt == n + 1 or (nxt is None and expected < n <= expected + max_skip):
            label, note = n, f"번호 기준 재조정 ({expected}→{n})"
        else:
            label, note = expected, f"OCR 오류 보정"
        results.append((label, w, True, note))
        expected = label + 1
        last_y = w[1]
    return results, expected

def split_pdf_by_numbers(pdf_bytes, two_col=True, margin=50, start_no=1, zoom=2.5, pad=6,
                         force_ocr=False, max_skip=2, lenient=False):
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    results = []
    expected = start_no

    for pno, page in enumerate(doc, start=1):
        W, H = page.rect.width, page.rect.height
        cols = [(0, W / 2), (W / 2, W)] if two_col else [(0, W)]
        sc = W / 595
        all_words, used_ocr = get_page_words(page, force_ocr)

        for x0, x1 in cols:
            words = [w for w in all_words if x0 - 1 <= w[0] < x1]
            words.sort(key=lambda w: (round(w[1]), w[0]))

            res, expected = find_question_starts(
                words, x0, margin * sc, expected, used_ocr, max_skip=max_skip, lenient=lenient
            )
            starts = [(n, w[1]) for n, w, ok, _ in res if ok]

            for i, (no, y) in enumerate(starts):
                top = max(0, y - pad * sc)
                bottom = (starts[i + 1][1] - pad * sc) if i + 1 < len(starts) else H - 30 * sc
                if bottom - top < 20 * sc:
                    continue
                z = min(zoom, 1400 / (x1 - x0))
                pix = page.get_pixmap(
                    matrix=fitz.Matrix(z, z),
                    clip=fitz.Rect(x0, top, x1, bottom),
                )
                results.append({"page": pno, "no": no, "data": pix_to_jpeg_bytes(pix)})
    return results

def debug_numbers(pdf_bytes, two_col=True, margin=50, start_no=1, force_ocr=False, max_pages=2,
                  max_skip=2, lenient=False):
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    out, expected = [], start_no
    for pno, page in enumerate(doc, start=1):
        if pno > max_pages:
            break
        W, H = page.rect.width, page.rect.height
        sc = W / 595
        cols = [(0, W / 2), (W / 2, W)] if two_col else [(0, W)]
        words, used_ocr = get_page_words(page, force_ocr)
        lines = []
        for x0, x1 in cols:
            col_words = [w for w in words if x0 - 1 <= w[0] < x1]
            col_words.sort(key=lambda w: (round(w[1]), w[0]))
            res, expected = find_question_starts(
                col_words, x0, margin * sc, expected, used_ocr, max_skip=max_skip, lenient=lenient
            )
            for n, w, ok, note in res:
                page.draw_rect(fitz.Rect(w[:4]), color=(0, 0.7, 0) if ok else (1, 0, 0), width=2 * sc)
                status = ("인정" + (f" ({note})" if note else "")) if ok else f"제외: {note}"
                lines.append(f"{pno}p 읽은 글자 '{w[4]}' → {n}번 : {status}")
            page.draw_line((x0 + margin * sc, 0), (x0 + margin * sc, H), color=(0, 0, 1), width=1 * sc)
        if two_col:
            page.draw_line((W / 2, 0), (W / 2, H), color=(0.5, 0.5, 0.5), width=1 * sc)
        z = min(1.5, 1400 / W)
        pix = page.get_pixmap(matrix=fitz.Matrix(z, z))
        out.append((pno, pix_to_jpeg_bytes(pix), used_ocr, lines))
    return out

def render_pdf_pages(pdf_bytes, zoom=2.0):
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    return [
        {
            "page": i,
            "no": None,
            "data": pix_to_jpeg_bytes(
                p.get_pixmap(matrix=fitz.Matrix(min(zoom, 1400 / p.rect.width), min(zoom, 1400 / p.rect.width)))
            ),
        }
        for i, p in enumerate(doc, start=1)
    ]

def image_to_jpeg_bytes(file_bytes):
    img = Image.open(io.BytesIO(file_bytes)).convert("RGB")
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=92)
    return buf.getvalue()

# ---------------------------------------------------------------------------
# 데이터베이스 연동 조작 함수 (추가/수정/삭제/체크)
# ---------------------------------------------------------------------------
def add_problem_to_db(subject, name, data, file_group, label):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""
        INSERT INTO problems (subject, file_name, file_group, label, data, checked)
        VALUES (?, ?, ?, ?, ?, 0)
    """, (subject, name, file_group, label, sqlite3.Binary(data)))
    conn.commit()
    conn.close()
    
    # 세션 동기화
    s_list, u_probs = load_data_from_db()
    st.session_state["subjects_list"] = s_list
    st.session_state["uploaded_problems"] = u_probs

def delete_problem_from_db(subject, pid):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("DELETE FROM problems WHERE id = ?", (pid,))
    conn.commit()
    conn.close()
    
    s_list, u_probs = load_data_from_db()
    st.session_state["subjects_list"] = s_list
    st.session_state["uploaded_problems"] = u_probs

def update_problem_check_in_db(pid, checked_val):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("UPDATE problems SET checked = ? WHERE id = ?", (1 if checked_val else 0, pid))
    conn.commit()
    conn.close()

def add_subject_to_db(new_subj):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO subjects (name) VALUES (?)", (new_subj,))
    conn.commit()
    conn.close()
    
    s_list, u_probs = load_data_from_db()
    st.session_state["subjects_list"] = s_list
    st.session_state["uploaded_problems"] = u_probs

def rename_subject_in_db(old_subj, new_subj):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("UPDATE subjects SET name = ? WHERE name = ?", (new_subj, old_subj))
    c.execute("UPDATE problems SET subject = ? WHERE subject = ?", (new_subj, old_subj))
    conn.commit()
    conn.close()
    
    s_list, u_probs = load_data_from_db()
    st.session_state["subjects_list"] = s_list
    st.session_state["uploaded_problems"] = u_probs

def delete_subject_from_db(target_subj):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("DELETE FROM subjects WHERE name = ?", (target_subj,))
    c.execute("DELETE FROM problems WHERE subject = ?", (target_subj,))
    conn.commit()
    conn.close()
    
    s_list, u_probs = load_data_from_db()
    st.session_state["subjects_list"] = s_list
    st.session_state["uploaded_problems"] = u_probs

def build_pdf(problems):
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    pw, ph = A4
    m = 40
    box_w, box_h = pw - 2 * m, ph - 2 * m
    for p in problems:
        img = Image.open(io.BytesIO(p["data"])).convert("RGB")
        iw, ih = img.size
        scale = min(box_w / iw, box_h / ih)
        w, h = iw * scale, ih * scale
        c.drawImage(ImageReader(img), m + (box_w - w) / 2, ph - m - h, width=w, height=h)
        c.showPage()
    c.save()
    return buf.getvalue()

def on_check(pid, key):
    val = st.session_state[key]
    update_problem_check_in_db(pid, val)
    # 메모리 세션 상태도 동기화
    for subj in st.session_state["uploaded_problems"]:
        for p in st.session_state["uploaded_problems"][subj]:
            if p["id"] == pid:
                p["checked"] = val

def set_all(subject, file_group, value):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""
        UPDATE problems SET checked = ? WHERE subject = ? AND file_group = ?
    """, (1 if value else 0, subject, file_group))
    conn.commit()
    conn.close()
    
    s_list, u_probs = load_data_from_db()
    st.session_state["subjects_list"] = s_list
    st.session_state["uploaded_problems"] = u_probs

# ---------------------------------------------------------------------------
# Streamlit UI 구성
# ---------------------------------------------------------------------------
st.set_page_config(page_title="과목별 오답 문제 정리", page_icon="📚", layout="wide")
st.title("📚 과목별 오답 문제 정리 홈페이지")
st.markdown("데이터베이스 연동으로 새로고침 시에도 데이터가 유지되는 오답노트 프로그램입니다.")

tab1, tab2, tab3, tab4 = st.tabs([
    "1️⃣ 업로드 및 자동 분할",
    "2️⃣ 과목 관리",
    "3️⃣ 과목별 누적 현황",
    "4️⃣ 오답 체크 및 PDF 정리",
])

# ---------------------------------------------------------------------------
# [탭 1] 업로드 및 자동 분할
# ---------------------------------------------------------------------------
with tab1:
    st.header("📤 과목 선택 및 문제 파일 업로드")

    if not st.session_state["subjects_list"]:
        st.warning("등록된 과목이 없습니다. [2️⃣ 과목 관리] 탭에서 과목을 먼저 추가해주세요.")
    else:
        col1, col2 = st.columns(2)
        with col1:
            selected_subject = st.selectbox("과목 선택", st.session_state["subjects_list"], key="tab1_subj")
        with col2:
            custom_filename = st.text_input("파일 이름", value="오답시험지", key="tab1_fname")

        uploaded_file = st.file_uploader(
            "문제 파일(PDF 또는 이미지)을 업로드하세요", type=["pdf", "png", "jpg", "jpeg"], key="tab1_uploader"
        )

        with st.expander("⚙️ 분할 설정 (잘 안 잘리면 여기를 조정하세요)"):
            two_col = st.checkbox("2단 편집 시험지 (좌/우 단을 나눠서 처리)", value=True)
            start_no = st.number_input("첫 문항 번호", min_value=0, max_value=999, value=1)
            margin = st.slider("번호 위치 허용 범위 (단 왼쪽 끝에서 pt)", 20, 150, 50)
            force_ocr = st.checkbox("OCR 강제 사용", value=False)
            max_skip = st.slider("번호 누락 허용 개수", 0, 5, 2)
            lenient = st.checkbox("번호 열에 있는 숫자는 순서가 달라도 문항 시작으로 인정", value=False)

        if uploaded_file is not None and st.button("🧩 문항 번호 기준 자동 분할 실행"):
            raw = uploaded_file.getvalue()
            ext = uploaded_file.name.split(".")[-1].lower()
            base = custom_filename or "오답시험지"
            
            existing_files = {p.get("file") for p in st.session_state["uploaded_problems"].get(selected_subject, [])}
            group, k = base, 2
            while group in existing_files:
                group, k = f"{base} ({k})", k + 1

            if ext == "pdf":
                try:
                    with st.spinner("문항을 찾는 중입니다..."):
                        pieces = split_pdf_by_numbers(
                            raw, two_col=two_col, margin=margin,
                            start_no=int(start_no), force_ocr=force_ocr,
                            max_skip=max_skip, lenient=lenient,
                        )
                except Exception as e:
                    st.error(f"PDF 처리 중 오류 발생: {e}")
                    pieces = []

                if pieces:
                    for p in pieces:
                        lbl = f"{p['no']}번"
                        add_problem_to_db(selected_subject, f"{base}_{p['no']}번 (p{p['page']})", p["data"], group, lbl)
                    st.success(f"✨ 문항 번호 {pieces[0]['no']}~{pieces[-1]['no']}번, 총 {len(pieces)}개로 분할했습니다.")
                else:
                    pages = render_pdf_pages(raw)
                    for p in pages:
                        lbl = f"p{p['page']} 전체"
                        add_problem_to_db(selected_subject, f"{base}_p{p['page']} (페이지 전체)", p["data"], group, lbl)
                    st.warning("문항 번호를 찾지 못해 페이지 전체를 그대로 저장했습니다.")
            else:
                try:
                    img_pdf = fitz.open(stream=raw, filetype=ext).convert_to_pdf()
                    with st.spinner("이미지에서 문항을 찾는 중입니다..."):
                        pieces = split_pdf_by_numbers(
                            img_pdf, two_col=two_col, margin=margin,
                            start_no=int(start_no), force_ocr=True,
                            max_skip=max_skip, lenient=lenient,
                        )
                except Exception as e:
                    st.error(f"이미지 처리 중 오류 발생: {e}")
                    pieces = []

                if pieces:
                    for p in pieces:
                        lbl = f"{p['no']}번"
                        add_problem_to_db(selected_subject, f"{base}_{p['no']}번", p["data"], group, lbl)
                    st.success(f"✨ 문항 번호 {pieces[0]['no']}~{pieces[-1]['no']}번, 총 {len(pieces)}개로 분할했습니다.")
                else:
                    add_problem_to_db(selected_subject, f"{base} (이미지 전체)", image_to_jpeg_bytes(raw), group, "이미지 전체")
                    st.warning("문항 번호를 찾지 못해 이미지 한 장 그대로 저장했습니다.")

        if uploaded_file is not None and st.button("🔍 번호 인식 결과 미리보기 (처음 2페이지)"):
            raw = uploaded_file.getvalue()
            ext = uploaded_file.name.split(".")[-1].lower()
            try:
                pdf_in = raw if ext == "pdf" else fitz.open(stream=raw, filetype=ext).convert_to_pdf()
                with st.spinner("분석 중..."):
                    results = debug_numbers(
                        pdf_in, two_col=two_col, margin=margin,
                        start_no=int(start_no), force_ocr=force_ocr or ext != "pdf",
                        max_skip=max_skip, lenient=lenient,
                    )
                for pno, img_bytes, used_ocr, lines in results:
                    st.markdown(f"**{pno}페이지**")
                    st.image(img_bytes, use_container_width=True)
                    st.code("\n".join(lines) if lines else "감지된 번호 없음")
            except Exception as e:
                st.error(f"미리보기 오류: {e}")

        st.markdown("---")
        st.subheader("👀 현재 과목에 등록된 문항 목록")
        problems = st.session_state["uploaded_problems"].get(selected_subject, [])
        if problems:
            st.write(f"총 {len(problems)}개의 문항이 등록되어 있습니다.")
            grid = st.columns(3)
            for i, prob in enumerate(problems):
                with grid[i % 3]:
                    st.caption(f"[ID {prob['id']}] {prob['file_name']}")
                    st.image(Image.open(io.BytesIO(prob["data"])), use_container_width=True)
                    if st.button("🗑️ 삭제", key=f"del_{selected_subject}_{prob['id']}"):
                        delete_problem_from_db(selected_subject, prob["id"])
                        st.rerun()
        else:
            st.info(f"'{selected_subject}' 과목에 등록된 문제가 없습니다.")

# ---------------------------------------------------------------------------
# [탭 2] 과목 관리
# ---------------------------------------------------------------------------
with tab2:
    sk1, sk2 = st.columns(2)

    with sk1:
        st.header("➕ 과목 추가")
        new_subject = st.text_input("추가할 과목명", placeholder="과목명 입력", key="tab2_input_add")
        if st.button("과목 추가하기", key="tab2_btn_add"):
            if not new_subject:
                st.warning("과목명을 입력해주세요.")
            elif new_subject in st.session_state["subjects_list"]:
                st.warning("이미 존재하는 과목입니다.")
            else:
                add_subject_to_db(new_subject)
                st.rerun()

    with sk2:
        st.header("✏️ 과목 수정 및 삭제")
        if not st.session_state["subjects_list"]:
            st.info("편집할 과목이 없습니다.")
        else:
            target = st.selectbox("수정/삭제할 과목", st.session_state["subjects_list"], key="tab2_select_target")
            rename_val = st.text_input("변경할 과목명", value=target, key="tab2_input_rename")

            b1, b2 = st.columns(2)
            with b1:
                if st.button("과목명 수정", key="tab2_btn_rename"):
                    if not rename_val:
                        st.warning("변경할 과목명을 입력해주세요.")
                    elif rename_val in st.session_state["subjects_list"]:
                        st.warning("이미 존재하는 과목명입니다.")
                    else:
                        rename_subject_in_db(target, rename_val)
                        st.rerun()
            with b2:
                if st.button("과목 삭제", key="tab2_btn_delete", type="primary"):
                    delete_subject_from_db(target)
                    st.rerun()

    st.markdown("---")
    st.subheader("📌 현재 등록된 과목")
    for subj in st.session_state["subjects_list"]:
        st.markdown(f"- **{subj}**")

# ---------------------------------------------------------------------------
# [탭 3] 과목별 누적 현황
# ---------------------------------------------------------------------------
with tab3:
    st.header("📊 과목별 오답 체크 현황")
    if not st.session_state["subjects_list"]:
        st.info("등록된 과목이 없습니다.")
    else:
        for subj in st.session_state["subjects_list"]:
            probs = st.session_state["uploaded_problems"].get(subj, [])
            by_file = {}
            for p in probs:
                if p.get("checked"):
                    f = p.get("file", "파일")
                    by_file[f] = by_file.get(f, 0) + 1
            c1, c2 = st.columns([3, 1])
            c1.markdown(f"### 📘 {subj}")
            c2.metric("체크한 오답 문항", f"{sum(by_file.values())}개")
            if by_file:
                st.caption(" · ".join(f"{f}: {c}개" for f, c in by_file.items()))
            st.divider()

# ---------------------------------------------------------------------------
# [탭 4] 오답 체크 및 PDF 정리
# ---------------------------------------------------------------------------
with tab4:
    st.header("📄 오답 체크 및 PDF 정리")
    if not st.session_state["subjects_list"]:
        st.info("등록된 과목이 없습니다.")
    else:
        pdf_subject = st.selectbox("과목 선택", st.session_state["subjects_list"], key="tab4_subj")
        subject_problems = st.session_state["uploaded_problems"].get(pdf_subject, [])

        if not subject_problems:
            st.info(f"'{pdf_subject}' 과목에 등록된 문제가 없습니다.")
        else:
            files = list(dict.fromkeys(p.get("file", "파일") for p in subject_problems))
            sel_file = st.selectbox("파일 선택", files, key=f"tab4_file_{pdf_subject}")
            file_probs = [p for p in subject_problems if p.get("file", "파일") == sel_file]

            st.write(f"### 📝 '{sel_file}' 오답 체크")
            b1, b2, _ = st.columns([1, 1, 3])
            b1.button("전체 체크", key=f"all_on_{pdf_subject}_{sel_file}",
                      on_click=set_all, args=(pdf_subject, sel_file, True))
            b2.button("전체 해제", key=f"all_off_{pdf_subject}_{sel_file}",
                      on_click=set_all, args=(pdf_subject, sel_file, False))

            per_row = 5
            for start in range(0, len(file_probs), per_row):
                row = st.columns(per_row)
                for col, prob in zip(row, file_probs[start:start + per_row]):
                    key = f"tab4_chk_{pdf_subject}_{prob['id']}"
                    if key not in st.session_state:
                        st.session_state[key] = prob.get("checked", False)
                    col.checkbox(
                        prob.get("label", prob["file_name"]),
                        key=key,
                        on_change=on_check,
                        args=(prob["id"], key),
                    )

            file_checked = sum(1 for p in file_probs if p.get("checked"))
            all_checked = sum(1 for p in subject_problems if p.get("checked"))
            m1, m2 = st.columns(2)
            m1.metric("이 파일에서 체크한 문항", f"{file_checked} / {len(file_probs)}개")
            m2.metric(f"'{pdf_subject}' 전체 체크 문항", f"{all_checked}개")

            st.markdown("---")
            scope = st.radio(
                "PDF에 넣을 범위",
                ["이 과목에서 체크한 문항 전체", "선택한 파일에서 체크한 문항만"],
                key=f"tab4_scope_{pdf_subject}",
            )
            targets = [
                p for p in subject_problems
                if p.get("checked")
                and (scope.startswith("이 과목") or p.get("file", "파일") == sel_file)
            ]

            pdf_key = f"generated_pdf_{pdf_subject}"
            if st.button("체크한 문항만 모아 오답노트 PDF 생성", key="tab4_pdf_btn"):
                if not targets:
                    st.warning("체크된 문항이 없습니다.")
                else:
                    st.session_state[pdf_key] = build_pdf(targets)
                    st.success(f"{len(targets)}개 문항으로 오답노트 PDF를 만들었습니다!")

            if pdf_key in st.session_state:
                st.download_button(
                    "📥 최종 오답노트 PDF 다운로드",
                    data=st.session_state[pdf_key],
                    file_name=f"{pdf_subject}_오답노트.pdf",
                    mime="application/pdf",
                    key="tab4_download",
                )
