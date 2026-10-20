from autostudio.office.markdown import inline, to_html


def test__inline__bold_code_link_and_escaping():
    assert inline('**Bold** and `a<b>` & <i>x</i>') == (
        '<strong>Bold</strong> and <code>a&lt;b&gt;</code> &amp; &lt;i&gt;x&lt;/i&gt;'
    )
    assert inline('see [terms](https://example.com/t)') == 'see terms (https://example.com/t)'
    # Внутри кода звёздочки — текст, а не разметка.
    assert inline('`**x**`') == '<code>**x**</code>'


def test__to_html__headings_paragraph_hr_and_quote():
    html = to_html('# Title\n\nFirst line\ncontinues.\n\n---\n\n> **Note** one\n> two\n\n## 1. Part')
    assert html.split('\n') == [
        '<h1>Title</h1>',
        '<p>First line continues.</p>',
        '<hr>',
        '<blockquote><p><strong>Note</strong> one two</p></blockquote>',
        '<h2>1. Part</h2>',
    ]


def test__to_html__nested_lists_and_continuations():
    text = (
        '7.7. **Price changes.**\n'
        '- A setup price does not change.\n'
        '- The Developer may change the price only in\n'
        '  two cases:\n'
        '  - (a) Meta changes its\n'
        '    prices;\n'
        '  - (b) the AI provider changes prices.\n'
        '- Notice is given.\n'
    )
    html = to_html(text)
    assert html.startswith('<p>7.7. <strong>Price changes.</strong></p>\n<ul><li>A setup price')
    assert '<li>The Developer may change the price only in two cases:<ul><li>(a) Meta changes its prices;</li>' in html
    assert '<li>(b) the AI provider changes prices.</li></ul></li><li>Notice is given.</li></ul>' in html


def test__to_html__ordered_list_start_and_type_switch():
    html = to_html('3. third\n4. fourth\n- bullet')
    assert html == '<ol start="3"><li>third</li><li>fourth</li></ol><ul><li>bullet</li></ul>'


def test__to_html__list_with_blank_line_between_items__one_list():
    html = to_html('- one\n\n- two\n\nAfter.')
    assert html.split('\n') == ['<ul><li>one</li><li>two</li></ul>', '<p>After.</p>']


def test__to_html__table_with_header_alignment_and_escaped_pipe():
    html = to_html('| # | Item | Price |\n|---:|---|---:|\n| 1 | A \\| B | <b>5</b> |\n')
    assert html == (
        '<table><thead><tr><th class="r">#</th><th>Item</th><th class="r">Price</th></tr></thead>'
        '<tbody><tr><td class="r">1</td><td>A | B</td><td class="r">&lt;b&gt;5&lt;/b&gt;</td></tr></tbody></table>'
    )


def test__to_html__table_without_separator__body_only():
    assert to_html('| a | b |') == '<table><tbody><tr><td>a</td><td>b</td></tr></tbody></table>'


def test__to_html__indented_item_before_top_level__nothing_lost():
    assert to_html('  - nested first\n- top') == '<ul><li>nested first</li></ul><ul><li>top</li></ul>'
