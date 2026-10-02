/**
 * 「跨区」标签：对方是对端地区（SZ ↔ SG）的账号。
 *
 * 全站唯一写法 —— 私信页、搜索结果、作者主页共用，文案和样式只改这里。
 * 额外属性原样透传到 <span>（如私信顶栏的 data-shed、搜索里的间距）。
 */
export default function RegionTag(props) {
  return <span className="dm-peer-tag" {...props}>跨区</span>
}
