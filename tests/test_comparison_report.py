from experiments.comparison_report import render


def test_private_report_escapes_model_content_and_keeps_runtime_evidence():
    result={'case':'example','engine':'baseline','status':'completed','seconds':1.2,'model_calls':1,'prompt_tokens':10,'output_tokens':5,'checks':{'synthetic_check':True},'history':{'completed':{'comparison.baseline':1}},'initial_context_sha256':'abc','turn':{'result':'<script>unsafe()</script>'},'artifacts':[{'title':'Example','body':'<img src=x onerror=unsafe()>'}]}
    html=render({'results':[result],'controls':['same model'],'limitations':['small smoke suite']})
    assert '<script>' not in html and '&lt;script&gt;' in html
    assert 'comparison.baseline: 1' in html and 'same model' in html
    assert '<img' not in html and '&lt;img' in html
