from __future__ import annotations

from lucode.gui.model_display import display_model_name


def test_display_model_name_prefers_catalog_model_name_over_internal_id():
    assert display_model_name("deepseek_v4_flash_model", "DeepSeek deepseek-v4-flash") == "deepseek-v4-flash"
    assert display_model_name("deepseek_v4_pro_model", "DeepSeek deepseek-v4-pro") == "deepseek-v4-pro"
    assert display_model_name("mimo_v2_5_model", "MiMo mimo-v2.5") == "mimo-v2.5"
    assert display_model_name("mimo_v2_5_pro_model", "MiMo mimo-v2.5-pro") == "mimo-v2.5-pro"


def test_display_model_name_cleans_internal_id_when_label_is_missing():
    assert display_model_name("deepseek_v4_pro_model", "") == "deepseek-v4-pro"
    assert display_model_name("mimo_v2_5_model", "") == "mimo-v2.5"
    assert display_model_name("gpt_5_5_model", "") == "gpt-5.5"
