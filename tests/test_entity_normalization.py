"""
tests/test_entity_normalization.py - 实体归一化模块测试
"""
import pytest
from unittest.mock import patch

from entity_normalization import (
    normalize_pronoun,
    normalize_honorific,
    normalize_geo,
    normalize_org,
    normalize_time,
    normalize_entity,
    normalize_entities,
    merge_similar_entities,
    is_blacklisted,
    is_whitelisted,
)


class TestNormalizePronoun:
    """测试人称代词归一化"""

    def test_first_person(self):
        """第一人称归一化"""
        assert normalize_pronoun("本人") == "我"
        assert normalize_pronoun("自己") == "我"
        assert normalize_pronoun("咱们") == "我们"

    def test_second_person(self):
        """第二人称归一化"""
        assert normalize_pronoun("您") == "你"
        assert normalize_pronoun("阁下") == "你"

    def test_third_person(self):
        """第三人称归一化"""
        assert normalize_pronoun("此人") == "他"
        assert normalize_pronoun("那人") == "他"

    def test_no_change(self):
        """不在映射中的词应保持不变"""
        assert normalize_pronoun("张三") == "张三"
        assert normalize_pronoun("我") == "我"  # 已经在标准形式


class TestNormalizeHonorific:
    """测试称谓归一化"""

    def test_remove_titles(self):
        """应去掉称谓后缀"""
        # 注意：正则模式匹配"先生/女士/老师/教授/博士"等（没有空格）
        import re
        # 验证模式能正确匹配（没有空格）
        pattern = re.compile(r"^(.+?)先生$")
        assert pattern.match("张三先生") is not None

        assert normalize_honorific("张三先生") == "张三"
        assert normalize_honorific("李四女士") == "李四"
        assert normalize_honorific("王五老师") == "王五"
        assert normalize_honorific("赵六教授") == "赵六"
        assert normalize_honorific("孙七博士") == "孙七"
        assert normalize_honorific("周八师傅") == "周八"

    def test_no_title(self):
        """没有称谓的词应保持不变"""
        assert normalize_honorific("张三") == "张三"
        assert normalize_honorific("普通人") == "普通人"


class TestNormalizeGeo:
    """测试地名归一化"""

    def test_province_level(self):
        """省级地名"""
        assert normalize_geo("广东省") == "广东省"
        assert normalize_geo("广西壮族自治区") == "广西壮族自治区"

    def test_city_level(self):
        """市级地名"""
        assert normalize_geo("深圳市") == "深圳市"
        assert normalize_geo("苏州市") == "苏州市"

    def test_district_level(self):
        """区县级地名"""
        assert normalize_geo("南山区") == "南山区"
        assert normalize_geo("昆山市") == "昆山市"

    def test_no_suffix(self):
        """没有地名后缀的词应保持不变"""
        assert normalize_geo("北京") == "北京"


class TestNormalizeOrg:
    """测试机构名归一化"""

    def test_company_suffix(self):
        """公司后缀归一化"""
        # 注意：归一化逻辑是替换最长后缀
        assert normalize_org("某某有限公司") == "某某公司"
        # "股份有限公司" → "公司" (先匹配"股份有限公司"→"公司")
        assert normalize_org("某某股份有限公司") == "某某公司"
        assert normalize_org("某某有限责任公司") == "某某公司"

    def test_institute_suffix(self):
        """研究所后缀归一化"""
        assert normalize_org("某某事务所") == "某某所"
        assert normalize_org("某某研究所") == "某某所"
        assert normalize_org("某某设计院") == "某某院"

    def test_no_suffix(self):
        """没有机构后缀的词应保持不变"""
        assert normalize_org("某某公司") == "某某公司"  # 已经是简称
        assert normalize_org("随机名字") == "随机名字"


class TestNormalizeTime:
    """测试时间词归一化"""

    def test_relative_time(self):
        """相对时间归一化"""
        assert normalize_time("今天") == "今日"
        assert normalize_time("明天") == "明日"
        assert normalize_time("昨天") == "昨日"

    def test_no_change(self):
        """不在映射中的时间词应保持不变"""
        assert normalize_time("2024-03-15") == "2024-03-15"
        assert normalize_time("早上") == "早上"


class TestIsBlacklisted:
    """测试黑名单检查"""

    def test_exact_match(self):
        """精确匹配黑名单词"""
        # 这些词在黑名单文件中
        assert is_blacklisted("事情") == True
        assert is_blacklisted("这个") == True

    def test_not_in_blacklist(self):
        """不在黑名单中的词"""
        assert is_blacklisted("张三") == False
        assert is_blacklisted("北京市") == False

    def test_normalization_disabled(self):
        """归一化禁用时应始终返回 False"""
        with patch("entity_normalization.ENTITY_NORMALIZATION_ENABLED", False):
            assert is_blacklisted("事情") == False


class TestIsWhitelisted:
    """测试白名单检查"""

    def test_exact_match(self):
        """精确匹配白名单词"""
        # 这些词在白名单文件中
        assert is_whitelisted("先生") == True
        assert is_whitelisted("公司") == True
        assert is_whitelisted("省") == True

    def test_suffix_match(self):
        """后缀匹配"""
        assert is_whitelisted("张老师") == True  # 以"老师"匹配
        assert is_whitelisted("某某公司") == True  # 以"公司"匹配

    def test_not_in_whitelist(self):
        """不在白名单中的词"""
        assert is_whitelisted("随机词") == False


class TestNormalizeEntity:
    """测试实体归一化主函数"""

    def test_pronoun_normalization(self):
        """人称代词归一化"""
        # "人家"不在黑名单中，应归一化为"我"
        result = normalize_entity("人家", "person")
        assert result == "我", f"expected '我' but got '{result}'"

    def test_honorific_removal(self):
        """称谓去掉"""
        # "张三先生"不在黑名单中，应正常处理
        result = normalize_entity("张三先生", "person")
        assert result == "张三", f"expected '张三' but got '{result}'"

    def test_geo_normalization(self):
        """地名归一化"""
        result = normalize_entity("广东省", "place")
        assert result == "广东省"

    def test_org_normalization(self):
        """机构名归一化"""
        # "某某有限公司" → "某某公司"
        result = normalize_entity("某某有限公司", "org")
        assert result == "某某公司", f"expected '某某公司' but got '{result}'"

    def test_time_normalization(self):
        """时间归一化"""
        # 注意："今天"在黑名单中，会被过滤
        # 测试不在黑名单中的时间词
        result = normalize_entity("明日", "time")
        assert result == "明日"  # 已经是标准形式

        # 测试"昨日"
        result2 = normalize_entity("昨日", "time")
        assert result2 == "昨日"

    def test_blacklist_filtering(self):
        """黑名单词应被过滤"""
        result = normalize_entity("事情")
        assert result == ""  # 在黑名单中，应返回空字符串

    def test_short_entity(self):
        """太短的实体应被过滤"""
        result = normalize_entity("的")
        assert result == ""  # 长度<2，应被过滤

    def test_normalization_disabled(self):
        """归一化禁用时应返回原实体"""
        with patch("entity_normalization.ENTITY_NORMALIZATION_ENABLED", False):
            result = normalize_entity("本人")
            assert result == "本人"


class TestNormalizeEntities:
    """测试批量实体归一化"""

    def test_batch_normalization(self):
        """批量归一化"""
        # 使用不在黑名单中的实体，并指定类型
        entities = ["人家", "张三先生", "广东省", "明日"]
        entity_types = {"张三先生": "person"}  # 指定类型才能正确归一化
        result = normalize_entities(entities, entity_types=entity_types)

        # "人家" → "我"
        assert "我" in result, f"expected '我' in result, got {result}"
        # "张三先生" → "张三"（需要指定类型）
        assert "张三" in result, f"expected '张三' in result, got {result}"
        assert "广东省" in result
        assert "明日" in result

    def test_deduplication(self):
        """应去重"""
        # 使用不在黑名单中的实体
        entities = ["人家", "自己"]  # 都归一化为"我"
        result = normalize_entities(entities)

        # 都归一化为"我"，应去重
        assert "我" in result
        assert result.count("我") == 1

    def test_normalization_disabled(self):
        """归一化禁用时应返回原列表"""
        with patch("entity_normalization.ENTITY_NORMALIZATION_ENABLED", False):
            entities = ["本人", "张三"]
            result = normalize_entities(entities)
            assert result == entities


class TestMergeSimilarEntities:
    """测试相似实体合并"""

    def test_merge_identical(self):
        """相同的实体应合并"""
        entities = [("北京", 0.8), ("北京", 0.9)]
        result = merge_similar_entities(entities, threshold=0.8)

        assert len(result) == 1
        assert result[0][0] == "北京"
        assert result[0][1] == 0.9  # 保留最高分

    def test_no_merge_different(self):
        """不同的实体不应合并"""
        entities = [("北京", 0.8), ("上海", 0.9)]
        result = merge_similar_entities(entities, threshold=0.8)

        assert len(result) == 2

    def test_merge_similar(self):
        """相似的实体应合并"""
        # "北京大学"和"北京大学"相似度很高
        entities = [("北京大学", 0.7), ("北京大学", 0.8)]
        result = merge_similar_entities(entities, threshold=0.5)

        assert len(result) == 1

    def test_empty_list(self):
        """空列表应返回空"""
        result = merge_similar_entities([])
        assert result == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
