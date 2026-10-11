# ---------------------------------------------------------------------------
# 데이터베이스 연동 조작 함수 (추가/수정/삭제/체크)
# ---------------------------------------------------------------------------
def add_problems_to_db(subject, items):
    """items: [(file_name, data, file_group, label), ...] 를 한 번에 저장"""
    if not items:
        return
    rows = [
        {"subject": subject, "file_name": n, "file_group": g,
         "label": lb, "data": d, "checked": 0}
        for n, d, g, lb in items
    ]
    with get_engine().begin() as conn:
        conn.execute(insert(problems_t), rows)
    refresh_session()


def delete_problem_from_db(subject, pid):
    with get_engine().begin() as conn:
        conn.execute(delete(problems_t).where(problems_t.c.id == pid))
    refresh_session()


def update_problem_check_in_db(pid, checked_val):
    with get_engine().begin() as conn:
        conn.execute(update(problems_t).where(problems_t.c.id == pid)
                     .values(checked=1 if checked_val else 0))


def add_subject_to_db(new_subj):
    with get_engine().begin() as conn:
        exists = conn.execute(
            select(subjects_t.c.name).where(subjects_t.c.name == new_subj)).first()
        if not exists:
            conn.execute(insert(subjects_t).values(name=new_subj))
    refresh_session()


def rename_subject_in_db(old_subj, new_subj):
    with get_engine().begin() as conn:
        conn.execute(update(subjects_t).where(subjects_t.c.name == old_subj)
                     .values(name=new_subj))
        conn.execute(update(problems_t).where(problems_t.c.subject == old_subj)
                     .values(subject=new_subj))
    refresh_session()


def delete_subject_from_db(target_subj):
    with get_engine().begin() as conn:
        conn.execute(delete(problems_t).where(problems_t.c.subject == target_subj))
        conn.execute(delete(subjects_t).where(subjects_t.c.name == target_subj))
    refresh_session()


def build_pdf(problems):
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    pw, ph = A4
    m = 40
    box_w, box_h = pw - 2 * m, ph - 2 * m
    for p in problems:
        img = Image.open(io.BytesIO(get_image(p["id"]))).convert("RGB")
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
    for subj in st.session_state["uploaded_problems"]:
        for p in st.session_state["uploaded_problems"][subj]:
            if p["id"] == pid:
                p["checked"] = val


def set_all(subject, file_group, value):
    with get_engine().begin() as conn:
        conn.execute(
            update(problems_t)
            .where(problems_t.c.subject == subject, problems_t.c.file_group == file_group)
            .values(checked=1 if value else 0)
        )
    refresh_session()
    # 이미 화면에 떠 있는 체크박스 상태도 같이 바꿔 줘야 '전체 체크/해제'가 화면에 반영됨
    for p in st.session_state["uploaded_problems"].get(subject, []):
        if p["file"] == file_group:
            st.session_state[f"tab4_chk_{subject}_{p['id']}"] = value


# ---------------------------------------------------------------------------
# 데이터베이스 연동 조작 함수 (추가/수정/삭제/체크)
# ---------------------------------------------------------------------------
def add_problems_to_db(subject, items):
    """items: [(file_name, data, file_group, label), ...] 를 한 번에 저장"""
    if not items:
        return
    rows = [
        {"subject": subject, "file_name": n, "file_group": g,
         "label": lb, "data": d, "checked": 0}
        for n, d, g, lb in items
    ]
    with get_engine().begin() as conn:
        conn.execute(insert(problems_t), rows)
    refresh_session()


def delete_problem_from_db(subject, pid):
    with get_engine().begin() as conn:
        conn.execute(delete(problems_t).where(problems_t.c.id == pid))
    refresh_session()


def update_problem_check_in_db(pid, checked_val):
    with get_engine().begin() as conn:
        conn.execute(update(problems_t).where(problems_t.c.id == pid)
                     .values(checked=1 if checked_val else 0))


def add_subject_to_db(new_subj):
    with get_engine().begin() as conn:
        exists = conn.execute(
            select(subjects_t.c.name).where(subjects_t.c.name == new_subj)).first()
        if not exists:
            conn.execute(insert(subjects_t).values(name=new_subj))
    refresh_session()


def rename_subject_in_db(old_subj, new_subj):
    with get_engine().begin() as conn:
        conn.execute(update(subjects_t).where(subjects_t.c.name == old_subj)
                     .values(name=new_subj))
        conn.execute(update(problems_t).where(problems_t.c.subject == old_subj)
                     .values(subject=new_subj))
    refresh_session()


def delete_subject_from_db(target_subj):
    with get_engine().begin() as conn:
        conn.execute(delete(problems_t).where(problems_t.c.subject == target_subj))
        conn.execute(delete(subjects_t).where(subjects_t.c.name == target_subj))
    refresh_session()


def build_pdf(problems):
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    pw, ph = A4
    m = 40
    box_w, box_h = pw - 2 * m, ph - 2 * m
    for p in problems:
        img = Image.open(io.BytesIO(get_image(p["id"]))).convert("RGB")
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
    for subj in st.session_state["uploaded_problems"]:
        for p in st.session_state["uploaded_problems"][subj]:
            if p["id"] == pid:
                p["checked"] = val


def set_all(subject, file_group, value):
    with get_engine().begin() as conn:
        conn.execute(
            update(problems_t)
            .where(problems_t.c.subject == subject, problems_t.c.file_group == file_group)
            .values(checked=1 if value else 0)
        )
    refresh_session()
    # 이미 화면에 떠 있는 체크박스 상태도 같이 바꿔 줘야 '전체 체크/해제'가 화면에 반영됨
    for p in st.session_state["uploaded_problems"].get(subject, []):
        if p["file"] == file_group:
            st.session_state[f"tab4_chk_{subject}_{p['id']}"] = value

# ---------------------------------------------------------------------------
# Streamlit UI 구성
# ---------------------------------------------------------------------------
st.set_page_config(page_title="과목별 오답 문제 정리", page_icon="📚", layout="wide")
require_password()
st.title("📚 과목별 오답 문제 정리 홈페이지")
st.markdown("데이터베이스 연동으로 새로고침 시에도 데이터가 유지되는 오답노트 프로그램입니다.")
if get_engine().dialect.name == "sqlite":
    st.warning("⚠️ 외부 DB가 연결되지 않아 이 서버의 임시 파일에 저장 중입니다. "
               "배포 서버가 재시작되면 데이터가 사라질 수 있어요.")

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
                    add_problems_to_db(selected_subject, [
                        (f"{base}_{p['no']}번 (p{p['page']})", p["data"], group, f"{p['no']}번")
                        for p in pieces
                    ])
                    st.success(f"✨ 문항 번호 {pieces[0]['no']}~{pieces[-1]['no']}번, 총 {len(pieces)}개로 분할했습니다.")
                else:
                    pages = render_pdf_pages(raw)
                    add_problems_to_db(selected_subject, [
                        (f"{base}_p{p['page']} (페이지 전체)", p["data"], group, f"p{p['page']} 전체")
                        for p in pages
                    ])
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
                    add_problems_to_db(selected_subject, [
                        (f"{base}_{p['no']}번", p["data"], group, f"{p['no']}번")
                        for p in pieces
                    ])
                    st.success(f"✨ 문항 번호 {pieces[0]['no']}~{pieces[-1]['no']}번, 총 {len(pieces)}개로 분할했습니다.")
                else:
                    add_problems_to_db(selected_subject, [
                        (f"{base} (이미지 전체)", image_to_jpeg_bytes(raw), group, "이미지 전체")
                    ])
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
                    st.image(get_image(prob["id"]), use_container_width=True)
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
