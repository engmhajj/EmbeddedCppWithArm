#include <iostream>

enum class Ecolor
{
    R,G
};
enum class Ecolor1
{
    R,G
};
int main() {
    if constexpr(Ecolor const color1= Ecolor::R; color1== Ecolor::R) {
        std::cout << "color is Res\n";
    }
    return 0;
}
