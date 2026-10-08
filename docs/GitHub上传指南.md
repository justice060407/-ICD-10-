# GitHub 上传指南

本目录已经初始化为 Git 仓库，并完成初始提交。由于当前机器没有 `gh` 命令，也没有可用的 GitHub Token，自动创建远端仓库会失败。推荐按下面步骤上传。

## 方式一：推送到你新建的空仓库

1. 在 GitHub 新建空仓库，建议名称：

```text
clinical-llm-course
```

2. 在本项目目录运行：

```powershell
cd D:\AI-Workspace\projects\clinical-llm-course
git branch -M main
git remote add origin https://github.com/<你的用户名>/clinical-llm-course.git
git push -u origin main
```

如果 `origin` 已经存在，先查看：

```powershell
git remote -v
```

再按实际地址改成：

```powershell
git remote set-url origin https://github.com/<你的用户名>/clinical-llm-course.git
git push -u origin main
```

## 方式二：网页上传 zip

如果暂时不想处理 Git 登录，可以使用项目 zip 包上传。注意 GitHub 网页上传不适合长期维护课程，后续更新还是建议使用 Git。

本项目不应上传以下目录：

```text
out/
out_*/
models/
__pycache__/
*.log
```

这些已经写入 `.gitignore` 和 `.dockerignore`。
